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
| CMS public use files (Medicare claims samples) | Claim lines, amounts, payment status | Base table, money amounts. **Loaded** (the synthetic file), see 2.2.2 |
| CMS Transparency in Coverage / payer transparency files | Payer-level rates and coverage terms | Payer behaviour features. Not loaded in Stage 1 |
| Healthcare.gov Marketplace issuer data | Issuer-level claim denial rates and reasons | Denial-rate priors per payer. **Loaded**, see 2.2.1 |
| HCUP / public discharge summaries | Diagnosis and procedure mixes | Realistic clinical context. Not loaded in Stage 1 |
| Hugging Face healthcare/insurance tabular sets | Extra volume | Augmentation only. Not loaded in Stage 1 |

Two sources are loaded, and they are enough for Stage 1: the claims sample gives the rows to
label and split, and the Marketplace file gives denial counts per issuer and plan. The other
three are not loaded and nothing depends on them yet. A later stage that needs one of them
verifies it first and adds its own section here.

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

### 2.2.2 Loaded source: CMS DE-SynPUF carrier claims

The source of claim-level rows. It is the CMS 2008-2010 Data Entrepreneurs' Synthetic Public
Use File (DE-SynPUF), published by CMS on its Medicare claims synthetic public use files
page. The "carrier" file holds claims from doctors and other non-institutional providers.

| Item | Value |
|---|---|
| File | One `.zip` holding one `.csv`, kept under `data/raw/claims/` (never committed) |
| Which file | Sample 1, Carrier Claims 1A (the DE-SynPUF has 20 samples; each carrier sample is split in two) |
| Size | About 113 MB zipped, 1.2 GB unzipped; 2,370,667 claims, 142 columns |
| Years | Claims from 2008 to 2010 |
| Grain in the file | One row per claim, with up to 13 service lines side by side (`HCPCS_CD_1` to `HCPCS_CD_13`, and so on) |
| Grain we store | One row per claim; one row per used service line |
| Lands in | `claim_samples` and `claim_sample_lines` (public reference tables, no `account_id`) |

**Synthetic data, and what it may be used for.** CMS describes the file as fully synthetic:
no beneficiary in it is an actual Medicare beneficiary. It was made by starting from real
"seed" beneficiaries and altering them, and the provider identifiers are random. CMS says
the file is for developing software and for training, and that it must not be used to draw
conclusions about the real Medicare population, because the alteration changed how the
variables relate to each other. No licence or terms-of-use statement was found on the CMS
page, in the codebook or in the FAQ; the file is published as a public use file.

**What is loaded.** Per claim: the claim id, the from and thru dates, and the claim's
diagnosis codes (up to 8; empty slots are dropped and the order is kept). Per service line:
the line number, the procedure code (HCPCS), the line diagnosis code, the line processing
indicator, and five amounts (Medicare payment, deductible, primary payer paid, coinsurance,
allowed charge).

**What is not loaded.** The beneficiary, physician and tax identifier columns; the other
DE-SynPUF files (beneficiary summary, inpatient, outpatient, prescription drugs, Carrier
Claims 1B); samples 2 to 20; and every claim after the load limit (see "How to load").

**Which line slots are used.** Each row has 13 line slots. A slot is in use exactly when it
has a tax number, which in the published file is also exactly when it has a processing
indicator; used slots always run from slot 1 without a gap. The procedure code is not a safe
test: it is empty on 5.78% of used lines. The tax number is read only for this test and is
not stored.

**Reading the numbers.** Several things in the published file are easy to get wrong:

- The processing indicator says how the line was handled: `A` means allowed, and the other
  values are reasons such as `C` (non-covered care), `N` (medically unnecessary) and `O`
  (other). But the indicator and the money are only loosely linked: about 20% of `A` lines
  are paid nothing, and about 46% of the other lines are paid something. A label must not
  assume that `A` means paid or that anything else means unpaid.
- 9 indicator values in the file (`H`, `G`, `K`, `2`, `J`, `1`, `=`, `E`, `0`) are not
  defined in the CMS codebook. They are kept as published.
- The amounts do not add up. Allowed charge equals payment plus deductible plus coinsurance
  plus primary payer paid on fewer than half of the lines, and the payment is above the
  allowed charge on about 13%. Every amount is a multiple of 10.00 and has a cap (payment
  stops at 550.00).
- There is no billed (submitted) amount, only allowed and paid amounts.
- Diagnosis codes are ICD-9, not ICD-10, and are written without the decimal point. Some
  cells hold placeholder words such as `XX000` or `OTHER`. A line's diagnosis is usually not
  one of the claim's diagnoses.
- CMS did no cleaning on the file, so oddities are expected.
- Many procedure codes are CPT codes. The CMS codebook notes that CPT codes and their
  descriptions are covered by an agreement between CMS and the American Medical Association,
  which holds the copyright. Only the codes are stored here, not their descriptions.

**Quality gates.** The file is rejected as a whole, and nothing is written to either table,
if any of these fail. They are checked on the rows that are read (see "How to load").

| Gate | Action |
|---|---|
| The file is not a readable `.zip` (damaged, encrypted), does not hold exactly one `.csv`, is not UTF-8 text, or cannot be parsed as csv | Reject |
| The header is not the expected 142 column names in order | Reject |
| A row does not have 142 fields; or there are no data rows | Reject |
| A cell has leading or trailing whitespace | Reject |
| Claim id is not 15 digits, or appears twice | Reject |
| A date is not a valid `YYYYMMDD` date, is outside 2008 to 2010, or from is after thru | Reject |
| An amount on a used line is not plain digits with exactly two decimals (such as `50.00`), or has more than 12 digits | Reject |
| A claim has no used line, or its used lines do not run from line 1 without a gap | Reject |
| A line has a tax number without an indicator, or an indicator without a tax number | Reject |
| An unused line slot holds a code or an amount other than `0.00` | Reject |
| The indicator is not exactly one character; a procedure code is not 5 characters; a diagnosis code is longer than 5 | Reject |
| The indicator is not in the CMS codebook list | Warn only; this occurs in the published file |
| The payment is above the allowed charge | Warn only; this occurs in the published file |
| A used line has no procedure code | Warn only; this occurs in the published file |

There is no gate on the amounts adding up, because the published file does not satisfy it.

**How to load.** With the database running and migrated:

```text
python3 -m pipelines.load_claims_sample data/raw/claims/<file>.zip
```

The command reads the first 50,000 claims of the file (about 100,000 service lines); pass
`--max-claims <n>` to read a different number. The full file is not loaded by default
because it has 2.4 million claims. The command checks every row it reads before it writes
anything, then writes the claims and their lines in one transaction. Exit code 0 means
loaded, 1 means the file was rejected (the first 20 problems are listed, with their row
numbers), 2 means a bad argument. Running the command again is safe: claims are matched on
the claim id and lines on the claim id and line number, and both are updated in place. Rows
are never removed. Loading 50,000 claims takes about 40 seconds.

Because only the first rows are read, the gates say nothing about the rest of the file: a
duplicate claim id or a damaged row further down is not noticed.

### 2.2.3 Labels: rule v1

The ML target is:

> `appeal_success` = 1 if an appeal on this denial would be paid, else 0.

That outcome cannot be observed: the claims sample holds no appeals. So the label is made by
a written rule and is a **proxy**. It is stored as `appeal_success_proxy`, and every report
that shows it must call it a proxy. The rule has a version, `v1`, which is stored on every
label row. Any change to the rule (a mapping, a band edge, a chance, the hash input) needs a
new version.

**Denied or not.**

- A service line is denied when its processing indicator is not `A` and its payment is 0.
- A claim is denied when at least one of its lines is denied.

Both conditions are needed because the indicator and the money are only loosely linked in
this file (see 2.2.2, "Reading the numbers"). Most denied claims are partly paid: only about
one in nine is denied on every line.

**Denial reason category.** Taken from the indicator of the claim's first denied line (the
lowest line number). A claim that is not denied has no category.

| Indicator on the first denied line | Category | CMS codebook meaning |
|---|---|---|
| `C` | `noncovered` | Noncovered care |
| `N` | `medical_necessity` | Medically unnecessary |
| `M` | `duplicate` | Multiple submittal, duplicate line item |
| `B` | `benefits_exhausted` | Benefits exhausted |
| `S`, `Q`, `T`, `U`, `V`, `X`, `Y` and the symbol codes (`!`, `@`, `<`, `>` and so on) | `coordination_of_benefits` | Secondary payer, or "MSP cost avoided": another payer is primary |
| Anything else, including `O`, `L`, `R`, `Z` and the values the codebook does not define | `other` | Other, CLIA, reprocessed, bundled test, or unknown |

**Appeal-success proxy.** Only denied claims have one. It is made in three steps:

1. Chance = the base chance of the category, plus a nudge for the band of the claim's total
   allowed charge (the sum over all its lines).
2. Draw = a number from 0 up to 1 made from the claim id: the first 8 bytes of the SHA-256
   hash of the text `v1:<claim id>`, read as a whole number and divided by 2^64.
3. The proxy is true when the draw is below the chance.

| Category | Base chance |
|---|---|
| `medical_necessity` | 0.60 |
| `other` | 0.45 |
| `noncovered` | 0.25 |
| `coordination_of_benefits` | 0.20 |
| `duplicate` | 0.10 |
| `benefits_exhausted` | 0.10 |

| Claim's total allowed charge | Nudge |
|---|---|
| Exactly 0 | -0.10 |
| Above 0, below 100 | 0.00 |
| 100 or more, below 250 | +0.05 |
| 250 or more | +0.10 |

**The base chances and nudges are assumptions, not measurements.** No public source gives
appeal win rates per reason for these claims. The numbers only encode an ordering that seems
reasonable, for example that a medical-necessity denial is more winnable than a duplicate.

The same claim always gets the same draw, so the label can be reproduced exactly. A draw is
used, and not a fixed threshold, because a threshold would make the label an exact formula
of two inputs: a model would re-learn the formula and score perfectly, which would prove
nothing. With the draw, the best a model can do is learn the chance, so its score on this
label has a ceiling well below a perfect one. Whether the Stage 3 target is reachable with
these chances has not been measured yet.

The claim's total allowed charge is used, and not the denied line's own amount, because the
allowed charge is 0 on about 88% of denied lines.

**What v1 leaves out.** The rule set was planned as reason category × payer denial rate ×
amount × prior authorisation. Version 1 uses two of the four:

| Input | In v1 | Why |
|---|---|---|
| Denial reason category | Yes | From the processing indicator |
| Amount | Yes | The claim's total allowed charge |
| Payer denial rate | No | The claims carry no issuer id, so they cannot be joined to `issuer_denial_stats` or `plan_denial_stats` |
| Prior authorisation | No | The claims file has no such field |

**Where it lands.** `claim_sample_labels`, a public reference table (no `account_id`) with
one row per claim: `is_denied`, `denial_reason_category`, `appeal_success_proxy`,
`label_rule_version`, and the split with its seed (see 2.5). A claim that is not denied has
an empty category and an empty proxy; the table refuses any other combination. A new rule
version overwrites the row.

**Measured on the first 50,000 claims** (the default load):

| Measure | Value |
|---|---|
| Claims denied | 5,325 (10.65%) |
| Appeal-success proxy true, among denied claims | 1,888 (35.46%) |
| Denied claims in `other` or `noncovered` | 4,665 (87.6%) |
| Denied claims in `benefits_exhausted` | 17 |

So the reason category carries little information: two categories hold almost nine in ten
denied claims. The set to model is also small, about 5,300 denied claims; loading more claims
is the way to grow it. These numbers describe a synthetic file and say nothing about real
Medicare claims.

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

**How the split is made.** Each claim is placed on its own, from a number between 0 and 1
made from a seed and the claim id (the first 8 bytes of the SHA-256 hash of the text
`split:<seed>:<claim id>`, divided by 2^64). Below 0.70 is train, below 0.85 is validation,
the rest is test. So:

- The same claim and seed always give the same split, however many other claims are loaded.
  A claim never moves from train to test when the data grows.
- The seed is stored with the split on every row of `claim_sample_labels`. The default is 42.
- The split does not depend on the label: its hash input differs from the label draw's.
- The split is not stratified, so the share of denied claims differs a little between splits.

Measured on the first 50,000 claims with seed 42:

| Split | Claims | Denied claims | Proxy true among denied |
|---|---|---|---|
| Train | 35,180 (70.36%) | 3,784 | 35.15% |
| Validation | 7,305 (14.61%) | 782 | 36.19% |
| Test | 7,515 (15.03%) | 759 | 36.23% |

## 2.6 Data quality gates

A batch is rejected if any of these fail:

- Required fields present on ≥ 99% of rows.
- No duplicate claim IDs.
- Amounts positive and inside a sane range.
- Denial-reason codes come from the known code list.
- Class balance between 20% and 80%. Applied to the model's target: the appeal-success proxy
  must be true for 20% to 80% of denied claims in the run. A run outside that range, or with
  no denied claim at all, is rejected and nothing is written; nothing is resampled. The share
  of claims that are denied is reported but not gated, because it is not a training target.

These are the general rules. Each loaded source lists the gates it actually applies in its
own section (2.2.1, 2.2.2); the class balance gate is applied by the pipeline (2.9). Where
the published file itself breaks a general rule (for example amounts of zero, or codes
outside the known list), the loader relaxes that rule or applies it as a warning instead of
a rejection, and the section says so.

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

## 2.9 Running the whole pipeline

One command loads both public files, labels and splits the claims, and prints a quality
report. It does **not** download the files: get them once by hand and keep them under
`data/raw/`.

| File | Download page | File link |
|---|---|---|
| Transparency in Coverage PUF, plan year 2026 | <https://www.cms.gov/marketplace/resources/data/public-use-files> | <https://download.cms.gov/marketplace-puf/2026/transparency-in-coverage-puf.zip> (a `.zip`; unzip it to get the `.xlsx` workbook) |
| DE-SynPUF Sample 1, Carrier Claims 1A | <https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-claims-synthetic-public-use-files/cms-2008-2010-data-entrepreneurs-synthetic-public-use-file-de-synpuf/de10-sample-1> | <https://downloads.cms.gov/files/DE1_0_2008_to_2010_Carrier_Claims_Sample_1A.zip> (keep it zipped) |

With the database running and migrated:

```text
python3 -m pipelines.run_data_pipeline data/raw/marketplace/<file>.xlsx \
    data/raw/claims/<file>.zip --plan-year 2026
```

| Option | Default | Meaning |
|---|---|---|
| `--plan-year` | none, required | Plan year of the workbook |
| `--max-claims` | 50,000 | How many claims to read from the start of the claims file |
| `--split-seed` | 42 | Seed of the train / validation / test split |

What it does, in order:

1. Reads and checks the workbook (the gates in 2.2.1).
2. Reads and checks the claims file (the gates in 2.2.2).
3. Labels every claim it read with rule v1 (2.2.3), places it in a split (2.5), and prints
   the quality report.
4. Applies the class balance gate (2.6).
5. Only if everything passed, writes `issuer_denial_stats`, `plan_denial_stats`,
   `claim_samples`, `claim_sample_lines` and `claim_sample_labels` in one transaction.

Exit code 0 means loaded, 1 means a file or the class balance was rejected, 2 means a bad
argument. On exit code 1 nothing is written to any table. Running the command again is safe:
every row is matched on its key and updated in place. With the default 50,000 claims it
takes about 50 seconds.

The report for the default run:

```text
label rule version: v1 (the appeal-success label is a proxy, not an observed outcome)
split seed: 42
claims labelled: 50,000
group: claims (share) | denied (of group) | proxy true (of denied)
train: 35,180 (70.36%) | 3,784 (10.76%) | 1,330 (35.15%)
validation: 7,305 (14.61%) | 782 (10.70%) | 283 (36.19%)
test: 7,515 (15.03%) | 759 (10.10%) | 275 (36.23%)
all: 50,000 (100.00%) | 5,325 (10.65%) | 1,888 (35.46%)
class balance gate (proxy true among denied claims, 20% to 80%): 35.46%, passes
```

Only the claims read in a run get a label. Claims loaded earlier with a larger
`--max-claims` keep the label they had, or none. The two single-source commands in 2.2.1 and
2.2.2 still work on their own; they load without labelling.
