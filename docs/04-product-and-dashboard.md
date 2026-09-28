# 4. Product & Dashboard

## 4.1 Accounts and roles

Anyone can sign up and create an **account** (a tenant). Everything they upload belongs to
that account and is invisible to every other account.

| Role | Can do |
|---|---|
| **Admin** | Everything in their account: invite users, set roles, see audit log and costs |
| **Specialist** | Upload denials, work the queue, edit and submit drafts for review |
| **Reviewer** | Approve or reject drafts, add comments |
| **Viewer** | Read dashboards only, no changes |

Enforcement is server-side. The UI hides what a role cannot do, but the API is what
actually decides.

## 4.2 Screens

### 1. Login / Sign-up

Email and password, with an invite flow for extra users in an account.

### 2. Overview

The "how are we doing" screen.

- Denials this period, total amount at risk.
- Appeal win rate, and money recovered.
- Breakdown by denial reason and by payer.
- Queue health: how many waiting, how many overdue.

### 3. Upload

- Drag and drop PDF, image or text.
- Live status per file: *Queued → Extracting → Scoring → Drafting → Needs review*.
- Failures show the reason and a Retry button.

### 4. Denial worklist

The screen a specialist lives in. A sortable table:

| Column | Note |
|---|---|
| Claim ID | |
| Payer | |
| Amount | |
| Denial reason | Extracted |
| Win probability | From the ML model |
| Expected value | Amount × probability |
| Deadline | Days left to appeal, red when close |
| Status | Where it is in the flow |

Default sort is expected value, highest first — work the money.

### 5. Denial detail — the split view

This is the key screen. Two panes:

- **Left: the document.** The original denial rendered in the browser, with the extracted
  fields highlighted on it. Click a field to jump to where it was found.
- **Right: the analysis.** Extracted fields (editable), win probability with the reasons
  behind it, the rules that fired, retrieved evidence, and the draft letter.

Every field shows a confidence score. Low confidence is flagged for the human to check.

### 6. Draft review

- The letter in an editor.
- Each citation is clickable and opens the exact evidence chunk it came from.
- Actions: **Edit**, **Approve**, **Reject with reason**.
- Approving marks it ready to send. **The system never sends anything itself.**

### 7. Admin

- Users and roles.
- Audit log, searchable.
- LLM spend against the monthly cap.
- Model versions currently live.

## 4.3 Rules the product must obey

1. **Nothing leaves without a human.** Approval is mandatory, always.
2. **No uncited claims.** If the letter states a policy fact, it must link to retrieved
   evidence, or the draft is blocked.
3. **Show the uncertainty.** Confidence and probability are displayed, never hidden.
4. **Everything is reversible.** Edits are versioned; the original is kept.
5. **Explain the score.** Every prediction shows the main factors behind it.

## 4.4 Build order

| Phase | Screens |
|---|---|
| 1 | Login, Upload, Worklist (basic) |
| 2 | Denial detail split view |
| 3 | Draft review, Overview |
| 4 | Admin, audit log, cost tracking |
