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
signs users up into one shared account, takes an uploaded denial letter through extraction,
rules, scoring, evidence and drafting, lets a reviewer edit the draft and an admin approve or
reject it. Each user sees only the letters they uploaded; an admin sees them all. The web
dashboard covers all of it in the browser: sign-up, login, the upload, the worklist, the
denial beside its draft, the review, and an overview. A user can also change or reset a
password, confirm their email with a 6-digit code, and sign in with Google. See the
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
   `APP_PUBLIC_URL=http://127.0.0.1:5173` (Google sign-in returns there), the shared
   account's name `SHARED_ACCOUNT_NAME`, your own email in `ADMIN_EMAILS` (only listed emails
   are admins, and only they, the emails in `SIGNUP_ALLOWED_EMAILS` and the domains in
   `SIGNUP_ALLOWED_DOMAINS` may sign up; set `INDIVIDUAL_SIGNUP_ENABLED=true` to let anyone
   else sign up into a personal account of their own, capped at `INDIVIDUAL_MONTHLY_UPLOADS`
   uploads a month each and `INDIVIDUAL_DAILY_UPLOADS` a day for all of them; it is off by
   default) and, on plain HTTP as here,
   `AUTH_COOKIE_SECURE=false`, or the browser never sends the login cookie back:
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
5. Open `http://127.0.0.1:5173/app/` and sign up with an email from `ADMIN_EMAILS`, then
   type the code from the newest file in `var/outbox/` to confirm the email and log in.

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
Every upload asks for a tick that the letter holds no real personal data: use made-up letters
only (architecture [3.18](docs/03-architecture.md#318-api-and-auth), "Individuals and personal
accounts").

Checks: `ruff check . && black --check . && mypy && pytest` for the backend (database tests
need `TEST_DATABASE_URL`), and `npm run check` in `frontend/` for the dashboard. How the
dashboard is built: architecture [3.21](docs/03-architecture.md#321-web-dashboard-part-1) and
[3.22](docs/03-architecture.md#322-web-dashboard-part-2).

## Running it in containers

The whole app (database, migrations, API, and a Caddy web server in front that serves the
screens) also runs from one compose file, the same one the production machine uses. You need
Docker and the trained model files in `models/`.

1. Make the settings file `deploy/prod/.env` (never committed) from `.env.example`. Beside the
   usual keys (including `SHARED_ACCOUNT_NAME` and `ADMIN_EMAILS`; keep
   `INDIVIDUAL_SIGNUP_ENABLED=false` until backups run), set `POSTGRES_PASSWORD`
   (URL-safe: `openssl rand -hex 24`), `SITE_ADDRESS=localhost` and
   `APP_PUBLIC_URL=https://localhost`. The site is served over HTTPS, so keep
   `AUTH_COOKIE_SECURE=true` and set `MAIL_BACKEND=smtp` with the `MAIL_SMTP_*` and `MAIL_FROM`
   keys (the outbox is refused with secure cookies).
2. Build the images and start everything:
   ```
   docker compose -f deploy/prod/compose.yml up -d --build
   ```
3. Check that `db`, `api` and `web` are healthy and `migrate` exited with 0:
   ```
   docker compose -f deploy/prod/compose.yml ps -a
   curl -k https://localhost/health
   ```
   The second line answers `{"status":"ok"}` (`-k` because `localhost` uses Caddy's own
   certificate).
4. Copy the evidence tables from the development database (started with `docker compose up -d`),
   once:
   ```
   docker compose exec -T db pg_dump -U app -d app --data-only \
     -t evidence_documents -t evidence_chunks -t evidence_chunk_embeddings \
     | docker compose -f deploy/prod/compose.yml exec -T db psql -U app -d app
   ```
5. Open `https://localhost/app/`, accept the certificate warning, sign up with an email from
   `ADMIN_EMAILS` and type the code from the email.

Stop it with `docker compose -f deploy/prod/compose.yml down`; the data stays in its volume
(`down -v` deletes it). How it is built: architecture
[3.23](docs/03-architecture.md#323-running-in-containers).

The API image installs exactly the packages in `deploy/api/requirements.lock` (each with its
version and hash). After any change to the dependencies in `pyproject.toml`, refresh the lock from
the repository root, inside the image's own base so the Linux / Python 3.11 packages are chosen,
then rebuild with step 2:
```
docker run --rm -v "$PWD/pyproject.toml:/w/pyproject.toml:ro" \
  -v "$PWD/README.md:/w/README.md:ro" -v "$PWD/deploy/api:/w/out" -w /w \
  python:3.11-slim@sha256:ae952e0a57138a4317b9b61caaa19a8217c2f356803f8d7e63aad08769950937 \
  sh -c "pip install -q --root-user-action=ignore pip-tools==7.6.2 && pip-compile -q \
    --upgrade --generate-hashes --strip-extras --allow-unsafe \
    --output-file out/requirements.lock pyproject.toml"
```
The same command is at the top of `deploy/api/Dockerfile`; if the base image's digest changes
there, change it here too.

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
