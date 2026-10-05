# 2. Data

## 2.1 The core difficulty

There is **no public dataset of real denial letters paired with appeal outcomes**. Say
that out loud early, because the whole data plan follows from it.

So we build the dataset in three layers:

| Layer | What it gives us | How real it is |
|---|---|---|
| **A. Public claims data** | Real denial rates, real reason categories, real money amounts | Real, but tabular — no documents |
| **B. Public policy & coding text** | The text an appeal argues against | Real |
| **C. Generated documents** | Denial letters and clinical notes that look like the real thing | Synthetic, built on top of A |

Layer A gives the **labels** for the ML model. Layer B gives the **evidence** for
retrieval. Layer C gives the **documents** for extraction and drafting.

## 2.2 Layer A — claims and denial outcomes

Candidate public sources. **Verify each before relying on it.**

| Source | What is in it | Use |
|---|---|---|
| CMS public use files (Medicare claims samples) | De-identified claim lines, amounts, payment status | Base table, money amounts |
| CMS Transparency in Coverage / payer transparency files | Payer-level rates and coverage terms | Payer behaviour features |
| Healthcare.gov Marketplace issuer data | Issuer-level claim denial rates and reasons | Denial-rate priors per payer. **Loaded**, see 2.2.1 |
| HCUP / public discharge summaries | Diagnosis and procedure mixes | Realistic clinical context |
| Hugging Face healthcare/insurance tabular sets | Extra volume | Augmentation only |

### 2.2.1 Loaded source: Marketplace Transparency in Coverage PUF

The first Layer A source that is actually loaded. It is a public use file published by CMS
on its Marketplace public use files page. It holds counts per issuer and per plan; it has
no claim lines and no patient data. Both levels are loaded.

| Item | Value |
|---|---|
| File | One `.xlsx` workbook, kept under `data/raw/marketplace/` (never committed) |
| Sheet read | `Transparency <plan year> - Ind QHP` (individual market medical plans) |
| Header row | Row 3. Rows 1 and 2 are a title and a legend |
| Grain in the file | One row per plan |
| Grain we store | One row per issuer and plan year; one row per plan and plan year |
| Lands in | `issuer_denial_stats` and `plan_denial_stats` (public reference tables, no `account_id`) |

**What is loaded, issuer level.** The issuer identity (id, name, state, exchange type, new
to the exchange or not) and the issuer-level counts: claims received, denied and
resubmitted, in and out of network; internal and external appeals filed and overturned,
with the two overturned percentages. These columns repeat on every plan row of an issuer,
so the loader keeps one row per issuer.

**What is loaded, plan level.** The plan identity (plan id, issuer id, state, plan type,
metal level) and the plan-level counts: claims received, denied and resubmitted, in and out
of network, and ten denial-reason counts (referral required, out of network, services
excluded, not medically necessary with and without behavioural health, benefit limit
reached, member not covered, investigational or cosmetic, administrative, other). Each plan
row points at its issuer row for the same plan year.

**What is not loaded.** The dental (`Ind SADP`) and small-business (`SHOP`) sheets, the
average monthly enrollment and disenrollment columns, and the URL columns.

**Missing values.** The file never leaves a number blank. It uses legend tokens instead:
`*` (not available), `**` (suppressed, small cell size), `***` (not required for the plan
type) and `N/A` (issuer or plan new to the Exchange).

- Issuer level: all four tokens are stored as empty (`NULL`).
- Plan level: a plan is either reported or not, and the table records which
  (`is_reported`). A plan whose count cells are all `N/A`, `***` or `*` is not reported and
  has no counts. On a reported plan, `**` is stored as empty, meaning suppressed, and a
  published `0` is kept as `0`. The two are different and must not be merged. Whether the
  issuer is new does not tell you whether a plan is reported: most unreported plans belong
  to existing issuers.

**Reading the plan-level numbers.** Two things in the published file are easy to get wrong:

- The denial reasons overlap. On most plans their sum is larger than the claims denied, so
  one claim appears to be counted under several reasons. A reason count is not a share of
  the denied total.
- Plan counts do not add up to the issuer counts. Where every plan of an issuer is
  reported, the plan total is usually lower than the issuer figure.

**Plan year.** The sheet has no year column; the year appears only in the sheet name. The
loader takes the plan year as an argument and reads the sheet named for that year, so a
year that does not match the file is rejected.

**Quality gates.** The file is rejected as a whole, and nothing is written to either
table, if any of these fail.

File and issuer rows:

| Gate | Action |
|---|---|
| The file is not a readable `.xlsx` workbook | Reject |
| The sheet for the given plan year, or a required header, is missing or renamed | Reject |
| There are no data rows | Reject |
| Issuer id is not five digits, state is not two letters, or exchange type is unknown | Reject |
| A count is negative or not a whole number; a percent is outside 0 to 100 or has more than two decimals | Reject |
| A number cell holds text that is neither a number nor a legend token, or holds TRUE/FALSE | Reject |
| Appeals overturned is greater than appeals filed | Reject |
| A count or percent cell is blank (the source always writes a number or a legend token) | Reject |
| Two plan rows of one issuer disagree on an issuer-level value | Reject |
| Out-of-network claims denied is greater than claims received | Warn only; this occurs in the published file |

Plan rows:

| Gate | Action |
|---|---|
| Plan id is not five digits, two letters and seven digits, or does not start with the row's issuer id and state | Reject |
| The same plan id appears twice | Reject |
| Plan type or metal level is unknown | Reject |
| A count is negative, not a whole number, blank, TRUE/FALSE, or text that is not a legend token | Reject |
| A row mixes reported values (numbers, `**`) with `N/A`, `***` or `*` | Reject |
| Claims denied is greater than claims received, in or out of network | Warn only; this occurs in the published file |
| Claims resubmitted is greater than claims received, in or out of network | Warn only; this occurs in the published file |
| One denial reason is greater than the total claims denied | Warn only; this occurs in the published file |
| The denial reasons add up to less than the claims denied | Warn only |

**How to load.** With the database running and migrated:

```text
python3 -m pipelines.load_marketplace_denials data/raw/marketplace/<file>.xlsx --plan-year 2026
```

The command checks the issuer rows and the plan rows before it writes anything, then
writes the issuers and the plans in one transaction, so the two tables always come from
the same file. Exit code 0 means loaded, 1 means the file was rejected (the first 20
problems are listed, with their row numbers), 2 means a bad argument. Running the command
again is safe: issuer rows are matched on issuer and plan year, plan rows on plan id and
plan year, and both are updated in place. Rows are never removed: an issuer or plan that
a later version of the file drops stays in the table.

**Label definition.** The ML target is:

> `appeal_success` = 1 if an appeal on this denial would be paid, else 0.

Because that outcome is not directly observable, derive it from a documented, versioned
rule set (denial reason category × payer denial rate × amount × whether prior auth
existed), then **record the rule version on every row**. This is a proxy label and must be
labelled as such in every report.

## 2.3 Layer B — evidence corpus for retrieval

Text the appeal letter can cite:

- Public payer medical-policy and coverage documents.
- CMS National and Local Coverage Determinations.
- Public code descriptors — ICD-10, CPT/HCPCS, and claim adjustment reason codes.
- Appeal-rights and timeline guidance published by regulators.

Stored chunked and embedded. **A generated citation is only allowed if it points at a
chunk that was actually retrieved.**

## 2.4 Layer C — generated documents

For each row in Layer A, generate:

- A **denial letter** in the format a payer would send, carrying the row's codes/amounts.
- A short **clinical note** consistent with the diagnosis and procedure.
- Where relevant, a **prior-authorisation record**.

Rules for generation:

- Vary layout, tone, and wording across several templates so the extractor cannot cheat.
- Inject realistic noise: scan artefacts, OCR errors, missing fields, wrong dates.
- Never copy a real patient record. All names, IDs and dates are fabricated.
- Store the generator version and the seed with every document, so any file can be
  reproduced exactly.

## 2.5 Splits

| Split | Share | Purpose |
|---|---|---|
| Train | 70% | Fit models |
| Validation | 15% | Tune, pick thresholds |
| Test | 15% | Reported numbers only, touched once per phase |

Split **by claim, never by document** — several documents can belong to one claim, and
leaking them across splits inflates every metric.

## 2.6 Data quality gates

A batch is rejected if any of these fail:

- Required fields present on ≥ 99% of rows.
- No duplicate claim IDs.
- Amounts positive and inside a sane range.
- Denial-reason codes come from the known code list.
- Class balance between 20% and 80% — otherwise resample.

## 2.7 Privacy

No real patient data ever enters this project. The generated documents are fictional. Even
so, treat uploads as sensitive: encrypt at rest, restrict by account, and keep an access
log.

## 2.8 Layout on disk

```text
data/
  raw/          downloaded public files, never edited by hand
  interim/      cleaned and joined tables
  processed/    model-ready features and splits
  documents/    generated denial letters and notes
  evidence/     policy corpus, chunked and embedded
```

`data/` is never stored with the project. Everything in it must be
reproducible by running a loader script.
