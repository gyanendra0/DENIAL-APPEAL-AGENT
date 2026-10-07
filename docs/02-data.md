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
an empty category and an empty proxy; the table refuses any other combination. The pipeline
replaces every label on each run (see 2.9), so the table always holds one rule version and
one split seed.

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
- Inject realistic noise: OCR-style character errors, scan-style layout damage and missing
  fields (2.4.4). Wrong dates are not injected: a wrong date is a contradiction between
  documents, not scan damage, and the answer key would then need two truths.
- Never copy a real patient record. All names, IDs and dates are fabricated.
- Store the generator version and the seed with every document, so any file can be
  reproduced exactly.

Built so far: the denial letter (2.4.1), the clinical note (2.4.2) and the prior-authorisation
record (2.4.3), and the noise step that every document then goes through (2.4.4). The stored
text is the noisy one. Sections 2.4.1 to 2.4.3 describe each document as its generator
writes it, before noise.

### 2.4.1 Generated so far: denial letters v1

**Every letter is fabricated.** The claim it describes comes from the synthetic claims sample
(2.2.2), and every name, id and letter date added on top is made up by the generator. No real
patient, provider or insurer appears in a letter.

**Which claims get a letter.** Only claims that label rule v1 calls denied (2.2.3), one letter
each. A claim that is not denied gets no document: on the default load that is 44,675 of the
50,000 claims.

**What comes from the claims sample.** These values are printed as they are stored:

| In the letter | Source |
|---|---|
| Claim number | `claim_samples.source_claim_id` |
| Date(s) of service | `claim_from_date` and `claim_thru_date`; one date when they are equal |
| Diagnosis codes | `diagnosis_codes`, in source order |
| One row per denied line | `line_number`, `hcpcs_code`, `allowed_charge_amount`, `payment_amount`, and a reason worded from the line's processing indicator |
| Claim totals | The allowed charge and the payment, each summed over all the claim's lines, paid ones included |
| Headline denial reason | The label's `denial_reason_category`, so the letter and the label always agree |

- A denied line is the label rule's denied line: indicator not `A` and payment 0. Paid lines
  are not listed one by one; they only count in the totals.
- A line with no procedure code is printed as "not provided". No code is made up. A claim
  with no diagnosis code gets the same wording (none of the denied claims loaded today).
- No money is made up either. The file has no billed amount, so the letter shows none, and a
  total of 0.00 is printed as 0.00.
- Codes are printed as codes, without descriptions (CPT descriptions are copyrighted, see
  2.2.2).
- The reason is one generic sentence per category, for example "the service is not covered".
  No policy text is quoted: that would be an invented policy statement.

**What is fabricated.** Picked by the seed from small word lists kept in the code:

| Field | How it is made |
|---|---|
| Patient name | One of 20 first names and one of 20 last names |
| Member id | 3 capital letters and 8 digits |
| Provider name | One of 8 made-up practice names |
| Payer name | One of 6 made-up insurer names |
| Reference number | `DL-` and 8 digits |
| Letter date | The last service date plus 7 to 45 days |
| Appeal deadline | The letter date plus 180 days |

The 180 days is made up like the rest. It is not the appeal window of any real plan or law.
The 14 payer and practice names were searched on the web on 2026-10-06 and none matched an
organisation of that exact name. A search cannot prove a name is unused, so check again
whenever a name is added.

**What a letter never shows.** The appeal-success proxy, its chance, the amount band, and the
train / validation / test split. Printing any of them would let the Stage 3 model read its
target off the page.

**Templates.** Four layouts, one picked per letter: `formal_letter` (paragraphs),
`benefits_table` (an explanation of benefits with a table of lines), `short_notice` and
`two_section` (the decision, then the appeal rights). All four print the same fields, in a
different order, under different labels, and with different date formats (`March 5, 2009`,
`03/05/2009`, `2009-03-05`), so an extractor cannot rely on one fixed position.

**The seed rule.** A run has one seed (default 42, from 0 to 2^31 - 1). Each choice in a
letter (the template, each name, each id, the letter date) is its own repeatable draw: a
number from 0 up to 1 made from the first 8 bytes of the SHA-256 hash of the text
`doc:<generator version>:<seed>:denial_letter:<claim id>:<name of the choice>`, divided by
2^64. So:

- A letter depends only on its claim, the seed and the generator version. The same three
  always give the same text, whichever other claims are generated and in whatever order.
- A different seed gives different made-up values for the same claim. The values taken from
  the claim do not change.
- The generator has a version, `v1`. Any change to a word list, a template, a date rule or
  the draw text needs a new version, because the same seed would no longer give the same
  text.
- The patient name, member id, provider name and payer name are the claim's **shared
  identity**: the clinical note (2.4.2) and the prior-authorisation record (2.4.3) print the
  same values, so a claim's documents describe the same people. Whichever document asks,
  these draws always use the letter's text above with version `v1`. A change to a word list
  or to that text therefore changes all three document types and needs a new version of
  each generator.

**The answer key.** Every value a letter prints, real or fabricated, is also stored as
structured fields beside the text. The generator builds the answer key first and writes the
text from it alone, so the text cannot hold a value the key lacks. Stage 3 marks the
extractor against the key; without it the made-up values would exist only inside the text
and could not be checked.

**Where it lands.** `generated_documents`, a public reference table (no `account_id`): it is
built only from the synthetic sample and holds no customer data. One row per claim and
document type, with `text`, `answer_key` (JSON; dates and amounts stored as text),
`template_id`, `seed`, `generator_version`, `noise_version` and `noise_record` (JSON, 2.4.4).
`text` holds the text after noise; the clean text is not stored. Nothing is written under
`data/`: every document is plain text in the database, with no files and no PDF.

The two noise columns were added by migration 0007. Both it and its downgrade remove the
stored documents (a clean row has no noise record to fill in), so run the command below once
after migrating.

**Generating.** With the claims loaded and labelled by the pipeline (2.9):

```text
python3 -m pipelines.generate_documents
```

| Option | Default | Meaning |
|---|---|---|
| `--seed` | 42 | Seed of every made-up value in the documents |

The command takes no file: it reads the claims, lines and labels already stored. One run
writes all three document types: a letter and a clinical note (2.4.2) for every claim
labelled denied, and a prior-authorisation record (2.4.3) for the claims that get one. Each
document goes through the noise step (2.4.4) before it is stored. The command
replaces the documents in one transaction: every existing row of `generated_documents`, of
every type, is removed and the documents of this run are stored. So the table always comes
from one run, with one seed, one generator version per type and one noise version, and a
claim that is no longer denied does not keep an old document.

It prints the total, one count per type with that type's generator version, and one line
about the noise: the noise version, the documents at each noise level, and how many lost a
field. For the default load:

```text
generated 13067 documents (seed 42)
  denial_letter: 5325 (generator v1)
  clinical_note: 5325 (generator v1)
  prior_auth: 2417 (generator v1)
  noise v1: none 2591, light 6549, heavy 3927; 1355 with a missing field
```

Exit code 0 means generated, 1 means the run was rejected, 2 means a bad argument. A run is
rejected when:

- no claim is labelled denied;
- the labels were made by another label rule version;
- a stored label no longer fits the claim's stored lines: the label rule, applied to the
  lines as they are stored now, does not give exactly the stored label (the lines were loaded
  again after the labels were made);
- after the documents are written, a claim labelled denied has no document (the coverage
  check, 2.4.4).

On exit code 1 nothing is written or removed; run the pipeline (2.9) again first. Running the
command again with the same seed leaves the same rows. Run it again after every pipeline run,
because new labels can change which claims are denied.

**Measured on the first 50,000 claims** (the default load, seed 42):

| Measure | Value |
|---|---|
| Letters | 5,325, one per denied claim, no two with the same text, before or after noise |
| Time | About 26 seconds for the whole command (all three types, 13,067 documents); most of it is the noise step (2.4.4) |
| Letters with a total allowed charge of 0.00 | 566 |
| Letters with no procedure code on the first denied line | 468 |

**Known limits.**

- The letters are less realistic on money than a real one: the allowed charge is 0 on about
  88% of denied lines, because that is what the file holds.
- The reason wording comes from a one-character indicator, so it is generic, and two
  categories (`other`, `noncovered`) are the headline reason of almost nine in ten letters.
- There are only four layouts, and the noise (2.4.4) is text-only with assumed rates, so
  extraction from these letters is probably still easier than from real, scanned ones.

### 2.4.2 Generated so far: clinical notes v1

**Every note is fabricated**, in the same way as the letter (2.4.1): the claim is synthetic
and every name and id added on top is made up. No real patient or provider appears in a note.

The note is the provider's own short record of the visit, written as if before the denial. It
knows nothing about the payer's decision.

**Which claims get a note.** Every claim that label rule v1 calls denied, one note each: the
same claims that get a letter.

**What a note prints.**

| In the note | Source |
|---|---|
| Date(s) of service | `claim_from_date` and `claim_thru_date`; one date when they are equal |
| Note date | `claim_thru_date`. It is not drawn |
| Diagnosis codes | `diagnosis_codes`, in source order |
| Procedure codes | `hcpcs_code` of **all** the claim's lines, denied or not, in line order, each code once |
| Patient name, member id, provider name | The claim's shared identity, the same values as in its letter (2.4.1) |

- Codes are printed as codes. No table of code meanings is loaded and CPT descriptions are
  copyrighted (2.2.2), so a description would be an invented clinical fact.
- The wording around the codes is generic, for example "seen for the conditions coded below".
  No symptoms, findings, medicines or history are made up.
- A line with no procedure code is left out of the list when another line of the claim has
  one, so the note then lists fewer codes than the claim has lines (1,028 of the 5,325 notes
  on the default load).
- A claim with no procedure code on any line, or with no diagnosis code, gets "not provided".
- The practice signs the note. There is no clinician name.

**What a note never shows.** Money, the denial reason, the payer, the claim number, and (as in
every document) the appeal-success proxy, its chance, the amount band and the split.

**Templates.** Three layouts, one picked per note: `visit_note` (sentences), `encounter_summary`
(a list of labelled fields) and `chart_entry` (short chart lines, with `Dx` and `Px` for the
codes). They print the same fields under different labels and with the three date formats of
the letter.

**The seed rule.** As in 2.4.1, with `clinical_note` in the draw text. The template is the
note's only own draw; the names and the member id come from the shared identity. The note
generator has its own version, `v1`, stored on its rows.

**The answer key.** Built first, with the text written from it alone, as for the letter.

**Measured on the first 50,000 claims** (the default load, seed 42):

| Measure | Value |
|---|---|
| Notes | 5,325, one per denied claim, no two with the same text, before or after noise |
| Notes that leave out a line without a procedure code | 1,028 |
| Notes with no procedure code at all | 4 |
| Notes whose patient, member id and provider equal the letter's | all 5,325 |

**Known limits.**

- The note is thin: codes and generic wording only. It gives the extractor fields to find,
  not clinical reasoning. Stage 4 drafting should not expect clinical detail in it.
- There are only three layouts. The notes are short, so the noise (2.4.4) changes them least:
  half of the notes at level `light` get no character error at all.

### 2.4.3 Generated so far: prior-authorisation records v1

**Every record is fabricated, more so than the other two.** The claims sample has no
prior-authorisation field at all, so the authorisation number, the request date, the decision
date and the status are all made up. No real patient, provider or insurer appears in a record.

The record is the payer's note of a request made before the service.

**Which claims get a record.** A claim that is denied and whose headline reason (the label's
`denial_reason_category`) is `medical_necessity` or `noncovered`. This is how "where relevant"
(2.4) is applied. The rule is our own choice, not a real rate of prior authorisation.

**What comes from the claims sample.**

| In the record | Source |
|---|---|
| Planned date(s) of service | `claim_from_date` and `claim_thru_date`; one date when they are equal |
| Diagnosis codes | `diagnosis_codes`, in source order |
| Requested procedure codes | `hcpcs_code` of each denied line whose own reason is `medical_necessity` or `noncovered`, in line order, one entry per line |
| Patient name, member id, provider name, payer name | The claim's shared identity, the same values as in its letter (2.4.1) |

A listed line with no procedure code is printed as "not provided". Paid lines, and denied
lines with another reason, are not listed.

**What is fabricated.**

| Field | How it is made |
|---|---|
| Authorisation number | `PA-` and 8 digits |
| Request date | The first service date minus 7 to 30 days |
| Decision date | The request date plus 1 to 5 days, so always before the service |
| Status | `approved` or `denied`, half each |

**The status carries no signal.** It is a seed draw only. It never reads the appeal-success
proxy, its chance, the amount band or the split, so an "approved" record next to a denial is
random here: it is not a real ground for appeal, and the Stage 3 model must not expect it to
help. A status tied to the label would leak the target instead. Whether a record exists
follows the headline reason, which the letter already prints, so that reveals nothing new.

**What a record never shows.** Money, the claim number, policy text (a quoted policy would be
an invented policy statement), and the proxy, its chance, the amount band and the split.

**Templates.** Three layouts, one picked per record: `authorization_notice` (a letter from the
payer), `request_summary` (a list of labelled fields) and `status_record` (short record lines).
They print the same fields under different labels and with the three date formats of the
letter.

**The seed rule.** As in 2.4.1, with `prior_auth` in the draw text. The record's own draws are
the template, the authorisation number, the two day counts and the status. The record
generator has its own version, `v1`, stored on its rows.

**The answer key.** Built first, with the text written from it alone, as for the letter.

**Measured on the first 50,000 claims** (the default load, seed 42):

| Measure | Value |
|---|---|
| Records | 2,417 (45.4% of the 5,325 denied claims), no two with the same text, before or after noise |
| Headline reason of those claims | `noncovered` 2,222, `medical_necessity` 195 |
| Status | 1,219 approved, 1,198 denied |
| Records with a "not provided" line | 287 |

**Known limits.**

- The status is random (see above), and the half-and-half share is an assumption.
- Almost all records belong to `noncovered` claims; `medical_necessity` is rare in the sample.
- There are only three layouts.

### 2.4.4 Noise v1

**Noise makes nothing up.** It only damages text that a generator already wrote: it adds no
name, code, amount or date. Every document is still fabricated, as in 2.4.1 to 2.4.3.

The generators write clean text, which no scanned page looks like. The noise step turns the
clean text of each document into the text that is stored, imitating what a poor scan and an
OCR engine do to a page. It is a separate step after the generators: they stay at `v1` and
their clean text is unchanged, character for character.

**Noise levels.** Each document draws one level:

| Level | Share | What the document gets |
|---|---|---|
| `none` | 20% | No character errors and no layout damage |
| `light` | 50% | Layout damage, and character errors at the low rates |
| `heavy` | 30% | Layout damage, and character errors at the high rates |

**Character errors.** Two kinds, each tested once per character:

| Kind | What happens | `light` | `heavy` |
|---|---|---|---|
| Swap | A look-alike character becomes its partner | 1% of look-alike characters | 3% |
| Drop | A character that is not a space or a line break disappears | 0.1% of those characters | 0.5% |

The look-alike pairs are fixed: `0` and `O`, `1` and `l`, `5` and `S`, `8` and `B`, `2` and
`Z`, `6` and `G`, each in both directions, plus `I` to `l` one way. Nothing else is swapped.
No character is inserted, and no letter pairs are merged (`rn` read as `m`).

**Layout damage.** Applied to every `light` and `heavy` document, before the character errors:

- A run of two or more spaces becomes one space. This removes the column alignment of the
  table-style templates and the indent of list lines.
- Lines longer than the document's page width are broken at a space. The width is drawn per
  document, from 60 to 100 characters. A word longer than the width stays whole.

Both keep every word. They only move where lines and columns fall. No template is added.

**Missing fields.** One document in ten loses exactly one field. This is its own draw, so it
can happen at any level, `none` included. The field is drawn from this list, each equally
likely:

| Document type | Fields that can go missing |
|---|---|
| `denial_letter` | member id, reference number, letter date, appeal deadline |
| `clinical_note` | member id |
| `prior_auth` | member id, authorisation number, request date, decision date |

- The value is removed wherever it is printed. Its label stays, like a form field left empty.
- The step finds the value by its printed form: the id itself, or the date in each of the
  three date formats. If the value is not printed in the text the step stops with an error
  and guesses nothing.
- Only fields that are printed in every document of their type, and whose printed form never
  equals or sits inside another value of the same document, are in the list.
- Never removed: the claim number, the patient, payer and provider names, the service dates,
  the diagnosis and procedure codes, the denial reason, the denied lines, the amounts and the
  prior-authorisation status. A document must stay tied to its claim and its reason. A swap
  or a drop can still hit any of them.

**The order.** The field is blanked first, then the layout is damaged, then characters are
dropped and swapped. Blanking first means the value is searched in clean text, where it is
sure to be found.

**The seed rule.** Noise uses the run's seed (`--seed`, 2.4.1); there is no separate option.
Each choice (the level, the missing field, the page width, each character test) is a
repeatable draw as in 2.4.1, made from the text
`doc:<noise version>:<seed>:<document type>:<claim id>:noise_<name of the choice>`. So:

- The noise of a document depends only on its claim, its type, the seed and the noise
  version. The same four always give the same stored text.
- The letter, the note and the record of one claim get different noise: one can be `heavy`
  and another `none`.
- Every choice name starts with `noise_`, so a noise draw can never equal a generator's draw.
- The noise has its own version, `v1`, stored on every row as `noise_version`. Any change to
  a rate, a share, the look-alike pairs, the list of fields or the draw text needs a new
  version, because the same seed would no longer give the same text.

**What noise never reads.** The appeal-success proxy, its chance, the amount band, the split
and the denial reason. The level and the missing field come from the seed, the claim id and
the document type only. If the damage followed the label, the Stage 3 model could read its
target from how damaged a page is.

**The answer key and the noise record.** The answer key is not changed by noise. It stays the
truth of the clean document: a claim number damaged by a swap is still the clean claim number
in the key, and the extractor is expected to repair it. What was done to the page is stored
beside it, in `noise_record`:

| Field | Meaning |
|---|---|
| `level` | `none`, `light` or `heavy` |
| `swaps` | How many characters were swapped |
| `drops` | How many characters were dropped |
| `missing_field` | The name of the blanked answer-key field, or empty |
| `page_width` | The drawn page width, or empty at level `none` |

Stage 3 needs the record for two things. A blanked field must be extracted as absent:
returning the answer-key value there would be a wrong answer. And accuracy can be reported
per level. The record does not say which values a swap or a drop hit.

**The coverage check.** After the documents are written, and in the same transaction, the
command (2.4.1) counts the claims labelled denied that have no row in `generated_documents`.
Above 0 the run is rejected: exit code 1, nothing saved, and the message names the count. The
check reads the stored rows, so it tests what was written and not how the rows were built.

**Measured on the first 50,000 claims** (the default load, seed 42; 13,067 documents):

| Measure | Value |
|---|---|
| Level `none` / `light` / `heavy` | 2,591 / 6,549 / 3,927 (19.8% / 50.1% / 30.1%) |
| Documents with a missing field | 1,355 (10.4%): 547 letters, 568 notes, 240 records |
| Of those, at level `none` | 284 |
| Characters swapped / dropped, all documents | 12,466 / 10,498 |
| Documents stored exactly as their clean text | 2,996 (22.9%) |
| `light` and `heavy` documents with at least one broken line | 52.3% |
| Denied claims with at least one document | 5,325 of 5,325 |
| Time | About 26 seconds for the whole command (about 4 before noise) |

Character errors (swaps plus drops) per document:

| Level | Type | Median | Mean | 9 in 10 have at most | Highest | With no error |
|---|---|---|---|---|---|---|
| `light` | `denial_letter` | 1 | 1.7 | 3 | 10 | 20.6% |
| `light` | `clinical_note` | 0 | 0.7 | 2 | 5 | 50.8% |
| `light` | `prior_auth` | 1 | 0.8 | 2 | 5 | 43.6% |
| `heavy` | `denial_letter` | 6 | 6.1 | 10 | 19 | 0.6% |
| `heavy` | `clinical_note` | 2 | 2.4 | 5 | 9 | 9.6% |
| `heavy` | `prior_auth` | 3 | 3.1 | 5 | 11 | 3.5% |

Share of letters where every printed copy of a value is left exactly as written (letters
where that field was blanked are not counted):

| Field | `light` | `heavy` |
|---|---|---|
| Claim number | 87.9% | 69.5% |
| Reference number | 92.2% | 75.0% |
| Member id | 93.2% | 79.9% |

**Known limits.**

- The rates and the shares are assumptions. No real scanned denial letter was measured.
- Whether the Stage 3 target (extraction at or above 85%) can be reached on `heavy`
  documents is not measured. The claim number is damaged in about three `heavy` letters in
  ten, so an extractor that copies characters exactly will fail there; it has to repair
  look-alikes. If the target cannot be reached, these rates are the first thing to revisit,
  with a new noise version.
- Noise is text-only: no image, no skew, no stains.
- Wrong dates are not injected (2.4).
- A blanked value leaves an odd sentence in the templates written as prose, for example
  "by ." in a letter. It reads less naturally than a form with an empty box.
- Collapsed spaces only change the templates that align with spaces. The others only get
  broken lines, and 2,996 documents are stored with no change at all.
- The clean text is not stored. To compare a noisy document with its clean form, run the
  generator again for that claim and seed.

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
  As long as the seed stays the same, a claim never moves from train to test when the data
  grows. A different seed gives a different split: on the first 1,000 claims, changing the
  seed from 42 to 7 moves 426 of them.
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
  documents/    generated documents kept as files (none yet, see below)
  evidence/     policy corpus, chunked and embedded
```

`data/` is never stored with the project. Everything in it must be
reproducible by running a loader script.

The generated documents (denial letters, clinical notes and prior-authorisation records) are
not files under `documents/`: they are stored as text in the `generated_documents` table
(2.4.1 to 2.4.4).

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
   `claim_samples`, `claim_sample_lines` and `claim_sample_labels` in one transaction. The
   first four are updated in place and rows are never removed. The labels are replaced:
   every existing label is removed and the labels of this run are stored.

Exit code 0 means loaded, 1 means a file or the class balance was rejected, 2 means a bad
argument. On exit code 1 nothing is written to or removed from any table. Running the
command again with the same files and options is safe: it leaves the same rows. With the
default 50,000 claims it takes about 50 seconds.

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

Because the labels are replaced, the labels table always comes from one run: one rule
version, one seed, and only the claims that run read. A run with a smaller `--max-claims`
therefore leaves the claims beyond its limit in `claim_samples` without a label, and a run
with another seed re-splits every claim it reads. Use one seed for everything that is later
compared.

The two single-source commands in 2.2.1 and 2.2.2 still work on their own; they load without
labelling. Loading claims that way after a pipeline run can change a claim's lines without
changing its label, so run the pipeline again afterwards.

The pipeline does not generate documents. That is a second command,
`python3 -m pipelines.generate_documents` (2.4.1), which reads what this one stored and
writes all three document types (2.4.1 to 2.4.3) with noise applied (2.4.4). Run it after
every pipeline run: the
documents are written from the stored claims and labels, so they go out of date when those
change.
