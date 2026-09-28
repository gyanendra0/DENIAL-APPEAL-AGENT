# 1. Problem & Scope

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
Denial document
      |
      v
[1] Read it        -> what kind of denial is this, which codes, which claim
      |
      v
[2] Score it       -> probability this appeal succeeds, and what it is worth
      |
      v
[3] Decide         -> appeal / do not appeal / send to a human to judge
      |
      v
[4] Build the case -> pull the policy text and records that support the argument
      |
      v
[5] Draft          -> write the appeal letter, cite the evidence
      |
      v
[6] Human approves -> person edits, approves or rejects. Nothing auto-sends.
```

Steps 1, 4 and 5 are GenAI. Step 2 is classical ML. Step 3 is rules plus the model. The
loop that runs 1 to 5 and calls tools along the way is the **agent**.

## 1.3 Who uses it

| User | What they want |
|---|---|
| **Billing specialist** | A ranked worklist of denials, and a draft letter already written |
| **Manager** | How many denials, how much money, how many appeals won |
| **Reviewer / clinician** | Check the clinical argument is not nonsense before it goes out |
| **Admin** | Manage accounts, see audit logs, control costs |

Each account only ever sees its own data.

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

## 1.5 Scope

### In scope (v1)

- Upload a denial document (PDF, image or text).
- Extract structured fields from it.
- Predict the chance an appeal succeeds.
- Retrieve supporting policy text.
- Draft an appeal letter with citations.
- Review, edit and approve on a dashboard.
- Accounts, roles, per-account data isolation.
- Audit log of every automated decision.

### Explicitly out of scope (v1)

- **Actually submitting** appeals to any insurer. The system produces a letter; a human
  sends it.
- Any connection to a live hospital system.
- Real patient data. Public and synthetic data only.
- Mobile apps.
- Any regulated clinical advice.

## 1.6 Success criteria

The project is a success when, on held-out data:

| Measure | Target |
|---|---|
| Correct denial-reason extraction | ≥ 85% |
| Win-prediction ranking quality (AUC) | ≥ 0.70 |
| Drafts a reviewer accepts with light edits | ≥ 60% |
| Time from upload to draft ready | < 2 minutes |
| Running cost | < $5/month |

## 1.7 Risks and honest limitations

| Risk | Mitigation |
|---|---|
| No public dataset of real denial letters with outcomes | Build a labelled set from public claims data plus generated documents — see doc 2 |
| The model invents a policy citation | Every citation must resolve to retrieved text, or the draft is blocked |
| Synthetic data makes results look better than reality | Report metrics on synthetic and real-derived splits separately, always |
| LLM cost creeps up | Hard monthly cap, small model by default, cache repeated prompts |
| Solo developer, limited time | Phased roadmap where every phase ships something usable on its own |
