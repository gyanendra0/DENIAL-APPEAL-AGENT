# Denial & Appeal Agent

An AI system that reads an insurance **claim denial**, works out **why** it was denied,
predicts **whether an appeal can win**, gathers the **supporting evidence**, drafts the
**appeal letter**, and puts it in front of a **human to approve** before anything is sent.

GenAI + classical ML + an agent loop, on top of public data.

## The idea in one line

> Insurance says *"no, we won't pay"*. This system finds out why, scores how winnable it
> is, writes the appeal, and a person clicks Approve.

## Why it is worth building

- **Real money.** A denied claim is unpaid work. Appeals are slow, manual and often
  skipped, so winnable cases get dropped.
- **Three techniques in one product.** Extraction (GenAI), win prediction (ML), and
  letter drafting with retrieval and tools (agentic).
- **Human stays in charge.** Nothing is sent without approval, and every automated
  decision is logged.

## How it works

```text
Denial document
      |
      v
[1] Read it        -> what kind of denial, which codes, which claim
      v
[2] Score it       -> probability this appeal succeeds, and what it is worth
      v
[3] Decide         -> appeal / do not appeal / send to a human to judge
      v
[4] Build the case -> pull the policy text and records that support the argument
      v
[5] Draft          -> write the appeal letter, cite the evidence
      v
[6] Human approves -> person edits, approves or rejects. Nothing auto-sends.
```

## Intended shape

| Thing | Direction |
|---|---|
| Built by | One person, end to end |
| Backend | Python, FastAPI |
| Database | PostgreSQL with pgvector |
| Frontend | React + Vite + TypeScript |
| LLM | Hosted API primary, open-weight fallback |
| Runtime | A single small always-on machine |
| Budget | Under $5/month all in |

## Documentation

| # | Doc | Answers |
|---|---|---|
| 1 | [Problem & Scope](docs/01-problem-and-scope.md) | What we are building, for whom, and what we are *not* building |
| 2 | [Data](docs/02-data.md) | Where the data comes from and how labels are created |
| 3 | [Architecture](docs/03-architecture.md) | Components, data flow, folder layout |
| 4 | [Product & Dashboard](docs/04-product-and-dashboard.md) | Screens, users, permissions |
| 5 | [Build Plan](docs/05-build-plan.md) | Stages, what each produces, when it is done |
| 6 | [Coding Conventions](docs/06-conventions.md) | Naming, structure and rules all code follows |

## Status

**Stages 0 to 5 are released (`v0.6.0`); Stage 6, deployment, is in progress.** The API
signs users up, takes an uploaded denial letter through extraction, rules, scoring, evidence
and drafting, and lets a reviewer edit, approve or reject the draft. The web dashboard covers
all of it in the browser: sign-up, login, the upload, the worklist, the denial beside its
draft, the review, and an overview. A user can also change or reset a password, confirm their
email with a 6-digit code, and sign in with Google. See the
[build plan](docs/05-build-plan.md).

## Running it locally

You need Docker, Python 3.11 or later and Node 26 (the version in `frontend/.nvmrc`).
Settings come from a `.env` file; `.env.example` lists every key.

1. Start the database (PostgreSQL + pgvector on port 5433):
   ```
   docker compose up -d
   ```
2. Install the backend and bring the database to the newest migration:
   ```
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   alembic upgrade head
   ```
3. Start the API on `http://127.0.0.1:8000`. It needs `AUTH_TOKEN_SECRET`,
   `APP_PUBLIC_URL=http://127.0.0.1:5173` (Google sign-in returns there) and, on plain
   HTTP as here, `AUTH_COOKIE_SECURE=false`, or the browser never sends the login cookie back:
   ```
   uvicorn src.api.app:create_app --factory
   ```
   With the default `MAIL_BACKEND=outbox`, the emails with the 6-digit code (one after
   sign-up, one for a password reset) are not sent: each is written as a `.eml` file in
   `var/outbox/`; open the newest one to find the code. To really send,
   set `MAIL_BACKEND=smtp` and the `MAIL_SMTP_*` and `MAIL_FROM` keys (architecture
   [3.18](docs/03-architecture.md#318-api-and-auth), "Sending email").
4. In a second terminal, start the dashboard. Its dev server forwards the API paths to
   `uvicorn`, so the browser sees one site:
   ```
   cd frontend
   npm ci
   npm run dev
   ```
5. Open `http://127.0.0.1:5173/app/` and sign up, then type the code from the newest file
   in `var/outbox/` to confirm the email and log in.

"Continue with Google" is optional and off until both `GOOGLE_CLIENT_ID` and
`GOOGLE_CLIENT_SECRET` are set. To turn it on (free, no model call):

1. In the [Google Cloud console](https://console.cloud.google.com/), create or pick a project.
2. Under "Google Auth Platform" (older consoles: "OAuth consent screen"), set the app name and
   a support email, choose the audience **External**, keep only the scopes `openid` and
   `email`, and add your own Google address as a test user. The app can stay in "Testing".
3. Under "Clients" (older consoles: "Credentials"), create an OAuth client of type **Web
   application** with the authorized redirect URI `APP_PUBLIC_URL` followed by
   `/auth/google/callback`, here `http://127.0.0.1:5173/auth/google/callback`. It must match
   exactly, or Google refuses the sign-in.
4. Put the client ID and secret in `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
   (never in a commit, an issue or a chat), and start `uvicorn` again. The login and sign-up
   screens then show the button (architecture
   [3.18](docs/03-architecture.md#318-api-and-auth), "Signing in with Google").

Uploading a letter calls a model and spends money (within the monthly cap in the settings).

Checks: `ruff check . && black --check . && mypy && pytest` for the backend (database tests
need `TEST_DATABASE_URL`), and `npm run check` in `frontend/` for the dashboard. How the
dashboard is built: architecture [3.21](docs/03-architecture.md#321-web-dashboard-part-1) and
[3.22](docs/03-architecture.md#322-web-dashboard-part-2).

## How work is split across machines

| Authoring machine | Run machine |
|---|---|
| Write code and documentation | `git init`, commit, push |
| Record design decisions | GitHub repo, rulesets, branch protection |
| Generate files | Install dependencies, database, run, test |
| — | CI and deployment |

Nothing is installed, executed or pushed from the authoring machine. Any command shown in
these documents is meant to be run on the **run machine**.

## What gets committed

Source code, published documentation, configuration templates and deployment manifests.

**Never committed:** secrets, datasets, trained models, dependency folders, and **working
documents** - task sheets, checklists and scratch notes live in `docs/working/` and stay
local. See `.gitignore`.

All work follows the same workflow: no direct commits to `main`, an issue before every
branch, one branch per feature group, pull request with green checks, squash merge.

> **Note on sources.** Dataset names, links and numbers in these documents are written
> from general knowledge. Verify each one before depending on it.
