# 1. Problem & Scope

This document describes the problem and the scope of v1.0.0 as built (released 2026-11-04).
The stages that built it are in [05-build-plan.md](05-build-plan.md).

## 1.1 The problem in plain words

A hospital or clinic treats a patient, then sends a bill (a **claim**) to the insurance
company. Often the insurer refuses to pay. That refusal is a **denial**, and it arrives as
a document full of codes and short phrases.

Someone then has to:

1. Read the denial and work out the real reason.
2. Decide whether it is worth fighting.
3. Find the paperwork that proves the claim was valid.
4. Write a letter arguing the case (the **appeal**).
5. Send it, then chase it.

This is slow, repetitive, and needs an expert. Many winnable denials are simply dropped
because nobody had time. That is the gap.

## 1.2 What the system does

```text
Denial letter (.txt or PDF with text)
      |
      v
[1] Read it        -> typed fields with a confidence each: reason, codes, amounts, dates
      |
      v
[2] Rules          -> appeal deadline and amount floor: pass / must review / stop
      |
      v
[3] Score it       -> probability this appeal succeeds; below the threshold, stop
      |
      v
[4] Build the case -> search the public policy text for evidence
      |
      v
[5] Draft          -> write the appeal letter; every policy statement quotes the evidence
      |
      v
[6] Human decides  -> a person edits, approves or rejects. Approved means stored, not sent.
```

Steps 1, 4 and 5 use a language model. Step 3 is a classical ML model. Step 2 is fixed
rules with no model. The loop that runs steps 1 to 5 and stores a trace of each step is
the **agent**. Every path through it ends in a stop for a person.

## 1.3 Who uses it

The system has three roles (`src/api/roles.py`). The people it was planned for map onto
them like this:

| Person | Role | What they can do |
|---|---|---|
| **Billing specialist** | `reviewer` | Upload letters, read their own jobs, edit drafts |
| **Reviewer / clinician** | `reviewer` | The same: check and edit the argument before a decision |
| **Manager** | `admin` | Read every job of the account, approve or reject drafts, see the costs |
| **Admin** | `admin` | The same; there is no separate admin screen (see 04) |
| **Read-only user** | `viewer` | Upload letters and read their own jobs; no editing |

- **Only an admin approves or rejects** a draft. Reviewers and admins edit; every role
  uploads.
- **Where roles come from:** a new user is stored as `reviewer`. `admin` comes only from the
  `ADMIN_EMAILS` setting, worked out on every request. There are no invites and no screen
  that sets a role.
- **Two kinds of account:**
  - **One shared account** for the team. Only emails or domains on the allow list may sign
    up to it.
  - **Personal accounts,** only while `INDIVIDUAL_SIGNUP_ENABLED` is on (off by default and
    in production). The person is `admin` of their own account and has monthly and daily
    upload caps, a "no personal data" tick on every upload, and "Delete my account".
- **Isolation:** an account sees only its own data. Inside an account, a user who is not an
  admin sees only the jobs of their own uploads.

## 1.4 Glossary

Short, non-technical definitions. Keep adding as new words appear.

| Term | Meaning |
|---|---|
| **Claim** | The bill sent to the insurer |
| **Denial** | The insurer's refusal to pay, in full or part |
| **Denial reason code** | A short code saying why, e.g. "not medically necessary" |
| **Appeal** | A letter arguing the denial was wrong |
| **Payer** | The insurance company |
| **Provider** | The hospital or clinic that gave the care |
| **Medical necessity** | The insurer's claim that the treatment was not needed |
| **Prior authorisation** | Permission the insurer wanted *before* treatment |
| **Evidence** | Policy text, notes or records that back up the appeal |
| **Win rate** | Share of appeals that end in payment |
| **Human in the loop** | A person must approve before anything is sent |
| **Proxy label** | A made-up "would this appeal win" answer from a fixed rule, used because no real outcomes are public |
| **Trace** | The stored record of every step the agent took for one job |

## 1.5 Scope

### In scope (v1, built)

- Upload one denial letter per upload: a UTF-8 `.txt` file or a PDF with a text layer. Only
  the text is kept, not the file.
- Extract typed fields from it, with a confidence score per field.
- Check fixed rules (appeal deadline, amount floor).
- Predict the chance an appeal succeeds.
- Retrieve supporting public policy text (CMS coverage determinations and manual chapters).
- Draft an appeal letter in which every policy statement quotes retrieved text. A draft whose
  quote is not found word for word is blocked.
- Review, edit and approve or reject on a dashboard in the browser.
- Accounts (one shared, optional personal ones), three roles, per-account isolation.
- An audit log of every person's actions (sign-up, upload, edit, approve, reject, password and
  sign-in changes). The automated decisions are in the stored agent trace of each job.
- Served continuously from one small server, with a nightly encrypted backup and an uptime
  alert.

### Out of scope (v1)

- **Actually submitting** appeals to any insurer. The system produces a letter; a human
  sends it.
- Any connection to a live hospital system.
- Real patient data. Public and synthetic data only.
- Mobile apps.
- Any regulated clinical advice.

### Limits of v1 (not built)

- **No images and no OCR.** A scanned PDF with no text layer is refused.
- **One letter per upload.**
- **`medical_necessity` letters were not drafted** in the Stage 5 measurements: the citation
  validator blocked every attempt (see 03, section 3.20, "Known limits").
- **No export and no sending.** Approved means stored.
- **A decision is final.** An approved or rejected draft cannot be reopened.
- The full list for v1.0.0 is in the "Known limits" of 03, sections 3.18 to 3.28.

## 1.6 Success criteria

The targets were set before the build. Each was measured when its stage was released.

| Measure | Target | Measured | Stage, date |
|---|---|---|---|
| Correct field extraction | ≥ 85% | 93.04% on the test split | 3, 2026-10-10 |
| Win-prediction ranking quality (AUC) | ≥ 0.70 | 0.7539 on the test split | 3, 2026-10-10 |
| Drafts a reviewer accepts with light edits | ≥ 60% | 23 of 30 = 76.67%, **scored by a model, not a person** | 4, 2026-10-16 |
| Time from upload to draft ready | < 2 minutes | 12.0 s, one browser run by a new user | 5, 2026-10-20 |
| Running cost | < $5/month | Oracle $0.00 + model $0.001617 in November so far | 6, 2026-11-04 |

- **Draft acceptance:** the owner decided on 2026-10-11 that a model scores the drafts against
  a written rubric. The earlier runs scored 0%, 0%, 56.67% and 66.67%.
- **AUC:** the label is a proxy from a fixed rule, and the best AUC that rule allows on the
  test split is 0.7522. So 0.7539 shows the model learned the rule, not that it predicts real
  appeals.

## 1.7 Risks and honest limitations

| Risk | What was done |
|---|---|
| No public dataset of real denial letters with outcomes | A labelled set built from public synthetic claims (CMS DE-SynPUF) plus generated letters, see doc 2. The appeal label is a proxy |
| The model invents a policy citation | Every quote must be found word for word in retrieved text, or the draft is blocked and not stored |
| Synthetic data makes results look better than reality | There is no real split to compare with: every claim is synthetic and every letter generated. The results measure the system against its own data, not real appeals |
| LLM cost creeps up | A hard monthly budget checked before every call, a small model (`gpt-4o-mini`) by default and an open-weight fallback. There is no prompt cache |
| Solo developer, limited time | Seven stages, each released on its own; see [05-build-plan.md](05-build-plan.md) |
