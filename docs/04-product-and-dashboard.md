# 4. Product & Dashboard

This document describes the dashboard as built in v1.0.0. The screens are in
`frontend/src/router.tsx`. The API behind them is in 03, sections 3.18 to 3.22.

## 4.1 Accounts and roles

There are two kinds of **account** (a tenant). Everything uploaded belongs to one account
and is invisible to every other account.

| Kind | Who joins it | Notes |
|---|---|---|
| **Shared** | Emails or domains on the allow list | One account for the team (`SHARED_ACCOUNT_NAME`) |
| **Personal** | Any other email, only while `INDIVIDUAL_SIGNUP_ENABLED` is on | Off by default and in production. Monthly and daily upload caps, a "no personal data" tick on every upload, "Delete my account" |

Roles (`src/api/roles.py`):

| Role | Upload and read jobs | Edit a draft | Approve or reject |
|---|---|---|---|
| **`admin`** | Every job of the account | Yes | Yes |
| **`reviewer`** | Only their own uploads | Yes | No |
| **`viewer`** | Only their own uploads | No | No |

- A new user is stored as `reviewer`.
- `admin` comes only from the `ADMIN_EMAILS` setting, worked out on every request. A stored
  `admin` whose email is no longer listed acts as `reviewer`. A personal-account user is
  `admin` of their own account.
- There are no invites and no screen or API route that sets a role. `viewer` is set only by a
  database edit.

Enforcement is server-side. The UI hides what a role cannot do, but the API is what
actually decides.

## 4.2 Screens

### 1. Sign-in screens

- **Login:** email and password, or Google sign-in. Repeated failed logins are limited per
  email and per IP address.
- **Sign-up:** email and password. The email is confirmed with a 6-digit code (**Verify
  email** screen).
- **Forgot password:** a 6-digit code is emailed, then a new password is set.
- **Google join:** the last step of a first Google sign-in. One deliberate click joins the
  account, so a Google click alone never creates a user.

### 2. Overview

The "how are we doing" screen.

- Letters uploaded, drafts waiting for review, approved (and its share of decided drafts),
  average time from upload to the end of the job.
- The model cost this month and in all, for admins only.
- Counts and charts: jobs by status, finished jobs by end state, drafts by review.

There is no money at risk, no win rate, no breakdown by payer and no overdue count: the
letters carry no outcome, and no deadline is tracked.

### 3. Upload

- A drop zone (or file picker) for one `.txt` or text-layer PDF. A scanned PDF is refused.
- The job's live status: `queued` → `running` → `done` or `failed`. A finished job shows its
  end state (draft waiting for review, no draft because the rules or the score stopped it, or
  the draft failed).
- A failure shows a fixed reason in plain words (for example "The monthly model budget is used
  up"), never the letter's text. There is no Retry button: upload the letter again.

### 4. Worklist

The screen a reviewer works in. A table of the jobs the user may see, newest first:

| Column | Note |
|---|---|
| File | The uploaded file's name; opens the job |
| Uploaded | When |
| Where it is | The job status, or its end state once finished |
| Review | Pending, approved or rejected |
| Reason | The denial reason read from the letter |
| Win chance | From the ML model |

- Filters on job status and review status. They are kept in the address, so a reload keeps
  them.
- The sidebar also lists the most recent jobs.

There is no payer, amount, expected value or deadline column.

### 5. Job detail: the split view

This is the key screen.

- **The letter:** its text as uploaded.
- **The analysis:**
  - The fields read from the letter, each with its confidence. A field to check says so in
    words, not only by colour.
  - The rules and their verdict, the win chance against the threshold a draft needs, and the
    evidence found (source, document, chunk, similarity).
- **The draft** (see screen 6) below the analysis.

Not built: fields highlighted on the letter, jumping from a field to where it was read, and
the reasons behind the score.

### 6. Draft review

Part of the job detail screen.

- The latest version of the letter, or the model's own draft. Each paragraph lists its
  citations with the quote they rest on.
- **Edit** (reviewers and admins). Saving checks the letter again with the same citation,
  placeholder and fact checks as the model's draft; a refused letter shows only the kind and
  place of each problem. A save from an old version is refused.
- **Approve** or **Reject with a reason** from a fixed list (admins only). The decision is
  final.
- Approving means stored. **The system never sends anything itself.**

Not built: opening the whole evidence chunk from a citation, a warning before leaving unsaved
edits, export.

### 7. Account

- Change password (this ends the user's other sessions).
- Delete my account, for a personal account only.

There is no Admin screen. The audit log (`GET /audit`) and the user lookup
(`GET /users/{id}`) are API only, for admins. The costs are on the Overview.

## 4.3 Rules the product must obey

1. **Nothing leaves without a human.** Approval is mandatory, always.
2. **No uncited claims.** If the letter states a policy fact, it must quote retrieved
   evidence, or the draft is blocked.
3. **Show the uncertainty.** Built: each field's confidence and the win chance are displayed,
   never hidden.
4. **Keep the original.** Built: edits are versioned (`draft_revisions`, the model's draft is
   version 0) and the model's draft can always be shown. A decision is final and cannot be
   undone.
5. **Explain the score.** Not built: the win chance is shown with the threshold, but not the
   factors behind it.

## 4.4 What was built when

| Stage | PR | Screens |
|---|---|---|
| 5 | #66 | Sign-up, login, upload with job status, worklist, overview |
| 5 | #68 | Job detail split view, draft editor, approve and reject |
| 6 | #71 | Warm theme, collapsible sidebar, two-column login |
| 6 | #73–#81 | Login limit, change password, forgot password, Google sign-in, email code |
| 6 | #85, #87 | Shared account, personal accounts and "Delete my account" |
| 6 | #89 | Account menu, file drop zone, overview charts, redesigned worklist, notifications |
