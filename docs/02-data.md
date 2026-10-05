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
no claim lines and no patient data.

| Item | Value |
|---|---|
| File | One `.xlsx` workbook, kept under `data/raw/marketplace/` (never committed) |
| Sheet read | `Transparency <plan year> - Ind QHP` (individual market medical plans) |
| Header row | Row 3. Rows 1 and 2 are a title and a legend |
| Grain in the file | One row per plan |
| Grain we store | One row per issuer and plan year |
| Lands in | `issuer_denial_stats` (public reference table, no `account_id`) |

**What is loaded.** The issuer identity (id, name, state, exchange type, new to the
exchange or not) and the issuer-level counts: claims received, denied and resubmitted, in
and out of network; internal and external appeals filed and overturned, with the two
overturned percentages. These columns repeat on every plan row of an issuer, so the loader
keeps one row per issuer.

**What is not loaded.** The dental (`Ind SADP`) and small-business (`SHOP`) sheets, and the
plan-level columns, including the denial-reason breakdown. The reasons are needed for the
labels and get their own loader.

**Missing values.** The file never leaves a number blank. It uses legend tokens instead:
`*` (not available), `**` (suppressed, small cell size), `***` (not required for the plan
type) and `N/A` (issuer new to the Exchange). All four are stored as empty (`NULL`).

**Plan year.** The sheet has no year column; the year appears only in the sheet name. The
loader takes the plan year as an argument and reads the sheet named for that year, so a
year that does not match the file is rejected.

**Quality gates.** The file is rejected as a whole, and nothing is written, if any of
these fail:

| Gate | Action |
|---|---|
| The file is not a readable `.xlsx` workbook | Reject |
| The sheet for the given plan year, or a required header, is missing or renamed | Reject |
| There are no data rows | Reject |
| Issuer id is not five digits, state is not two letters, or exchange type is unknown | Reject |
| A count is negative or not a whole number; a percent is outside 0 to 100 or has more than two decimals | Reject |
| A cell holds text that is neither a number nor a legend token | Reject |
| Appeals overturned is greater than appeals filed | Reject |
| A count or percent cell is blank (the source always writes a number or a legend token) | Reject |
| Two plan rows of one issuer disagree on an issuer-level value | Reject |
| Out-of-network claims denied is greater than claims received | Warn only; this occurs in the published file |

**How to load.** With the database running and migrated:

```text
python3 -m pipelines.load_marketplace_denials data/raw/marketplace/<file>.xlsx --plan-year 2026
```

Exit code 0 means loaded, 1 means the file was rejected (the first 20 problems are
listed, with their row numbers), 2 means a bad argument. Running the command again is safe: rows are
matched on issuer and plan year and updated in place.

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
