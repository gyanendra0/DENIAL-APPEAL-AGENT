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
- **Human stays in charge.** Nothing is sent anywhere, every automated step is kept in a
  stored trace, and every action a person takes is in the audit log.

## How it works

```text
Denial letter (.txt or a PDF with text)
      |
      v
[1] Read it        -> the denial reason, the amounts, the dates
      v
[2] Check rules    -> appeal window closed or amount too small: stop here
      v
[3] Score it       -> probability this appeal succeeds
      v
[4] Build the case -> search the public policy text that supports the argument
      v
[5] Draft          -> write the appeal letter; every citation is checked against the evidence
      v
[6] Human reviews  -> a reviewer edits, an admin approves or rejects. Nothing is sent.
```

## Shape as built

| Thing | As built in v1.0.0 |
|---|---|
| Built by | One person, end to end |
| Backend | Python, FastAPI |
| Database | PostgreSQL 16 with pgvector |
| Frontend | React + Vite + TypeScript, served by Caddy |
| LLM | `gpt-4o-mini` (and `text-embedding-3-small`) primary, an open-weight model as fallback, under a monthly budget cap |
| Runtime | One Oracle A1 machine (Always Free) with k3s and Argo CD |
| Budget | Under $5/month all in (measured: Oracle $0.00, model $0.001617) |

## Documentation

| # | Doc | Answers |
|---|---|---|
| 1 | [Problem & Scope](docs/01-problem-and-scope.md) | What we are building, for whom, and what we are *not* building |
| 2 | [Data](docs/02-data.md) | Where the data comes from and how labels are created |
| 3 | [Architecture](docs/03-architecture.md) | Components, data flow, folder layout |
| 4 | [Product & Dashboard](docs/04-product-and-dashboard.md) | Screens, users, permissions |
| 5 | [Build Plan](docs/05-build-plan.md) | Stages, what each produces, when it is done |
| 6 | [Coding Conventions](docs/06-conventions.md) | Naming, structure and rules all code follows |
| 7 | [Setup Guide](docs/07-setup-guide.md) | How to rebuild the server and the app from nothing |

## Status

**All seven stages are released. `v1.0.0` (2026-11-04) is the current version.** It is served at
`https://denial-appeal.duckdns.org` from one Oracle A1 machine running k3s and Argo CD, with a
nightly encrypted backup off the machine and an uptime alert.

- **What it does.** Users sign up into one shared account (allow-listed emails only).
  Individual sign-up into personal accounts exists but is off in production. A user uploads a
  denial letter; a background job takes it through extraction, rules, scoring, evidence and
  drafting. A reviewer edits the draft and an admin approves or rejects it. Each user sees only
  the letters they uploaded, and an admin sees them all.
- **Accounts.** A user can change or reset a password, confirm their email with a 6-digit code
  and sign in with Google.
- **Measured.** Each stage's target was met:

  | Target | Result |
  |---|---|
  | Extraction accuracy ≥ 85% | 93.04% |
  | Model AUC ≥ 0.70 | 0.7539, on a proxy label |
  | Drafts accepted ≥ 60% | 76.67% (23 of 30), scored by a model, not a person |
  | Upload to draft < 2 minutes | 12.0 s |
  | Cost < $5/month | Oracle $0.00, model $0.001617 |
  | Test coverage ≥ 70% | 99.87% (4,132 Python tests), 99.79% of frontend lines (479 tests) |

- **Known limits.** One API process, one letter per upload, no OCR, letter text not encrypted
  at rest by the app, and up to 24 hours of data at risk between backups. The full list is in
  [architecture, "Known limits of v1.0.0"](docs/03-architecture.md#known-limits-of-v100).

Each stage's plan and result: the [build plan](docs/05-build-plan.md).

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
screens) also runs from one compose file, which stays the fallback for the production machine.
You need Docker and the trained model files in `models/`.

1. Make the settings file `deploy/prod/.env` (never committed) from `.env.example`. Beside the
   usual keys (including `SHARED_ACCOUNT_NAME` and `ADMIN_EMAILS`; keep
   `INDIVIDUAL_SIGNUP_ENABLED=false`: Compose has no backups), set `POSTGRES_PASSWORD`
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

The API image installs exactly the packages in `deploy/api/requirements.txt` (each with its
version and hash). After any change to the dependencies in `pyproject.toml`, refresh the lock from
the repository root, inside the image's own base so the Linux / Python 3.11 packages are chosen,
then rebuild with step 2:
```
docker run --rm -v "$PWD/pyproject.toml:/w/pyproject.toml:ro" \
  -v "$PWD/README.md:/w/README.md:ro" -v "$PWD/deploy/api:/w/out" -w /w \
  python:3.11-slim@sha256:ae952e0a57138a4317b9b61caaa19a8217c2f356803f8d7e63aad08769950937 \
  sh -c "pip install -q --root-user-action=ignore pip-tools==7.6.2 && pip-compile -q \
    --upgrade --generate-hashes --strip-extras --allow-unsafe \
    --output-file out/requirements.txt pyproject.toml"
```
The same command is at the top of `deploy/api/Dockerfile`; if the base image's digest changes
there, change it here too.

## Security checks

Every pull request runs zizmor (workflow files), Trivy (libraries, secrets, Dockerfiles), a Trivy
scan of both built images, the tests on the locked versions, and checks of the Kubernetes files.
What blocks and why: architecture
[3.24](docs/03-architecture.md#324-checks-on-every-pull-request).

To run them locally before pushing, install the versions set in
`.github/workflows/security.yml`, then from the repository root:
```
GH_TOKEN="$(gh auth token)" zizmor .
trivy fs --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 .
trivy fs --scanners secret --secret-config trivy-secret.yaml --exit-code 1 .
trivy config --severity HIGH,CRITICAL --exit-code 1 .
docker build -f deploy/api/Dockerfile -t denial-appeal-agent-api .
trivy image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 denial-appeal-agent-api
docker build -f deploy/web/Dockerfile -t denial-appeal-agent-web .
trivy image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 denial-appeal-agent-web
```

Two things are set by hand in GitHub, once:
1. **Settings > Advanced Security:** turn on secret scanning and push protection.
2. **Settings > Rules > Rulesets**, for `main`: require a pull request and the nine status checks
   `checks`, `lock-tests`, `frontend`, `zizmor`, `trivy`, `image-scan`, `manifests`, `smoke` and
   `backup-restore`. Choose **GitHub Actions** as the source of each one: a pull request also shows
   code-scanning results named `zizmor` and `Trivy`, and those are not the gates. Because
   `manifests` and `smoke` are required, a bot deploy pull request also builds both images, about
   10 minutes more on each deploy.

Dependabot has opened pull requests for npm only so far. Whether it updates the hashed
`deploy/api/requirements.txt`, stays on Python 3.11 and reads `deploy/prod/compose.yml` is still
open: architecture [3.24](docs/03-architecture.md#324-checks-on-every-pull-request), "Known
limits". Its pull requests are listed under **Insights > Dependency graph > Dependabot**.

## Publishing the images

Each merge to `main` builds, scans, signs and publishes both images to GHCR, then opens a pull
request with the new digests, which merges itself. How and why: architecture
[3.25](docs/03-architecture.md#325-publishing-the-images).

Set by hand in GitHub, once:
1. **A GitHub App** on this repository with **Contents** and **Pull requests** set to read and write.
2. **Settings > Environments:** an environment `deploy-bot`, limited to the branch `main`, with the
   variable `DEPLOY_BOT_APP_ID` and the secret `DEPLOY_BOT_PRIVATE_KEY`.
3. **Settings > General:** allow auto-merge.

To pause deploys, turn off auto-merge on the open deploy pull request; later builds then open
nothing. To go on, close that pull request, then re-run `deploy-pr` of the newest `images` run, or
let the next merge that builds deploy. To roll back, revert a merged one.

Re-run only the newest `images` run: an older one opens a deploy pull request that moves production
back, and it merges by itself. Never re-run after a rollback, or to resume from a build you paused
because it is bad: merge the fix and let that build deploy. A rollback lasts only until the next
merge that builds, so the fix has to be that merge. A rollback past a database migration needs
`alembic downgrade` from the newer image first: the older image does not know that revision, so its
migration should fail and the deploy stop.

Anyone can check an image with no login (the capitals in the repository name matter):
```
cosign verify ghcr.io/gyanendra0/denial-appeal-agent/api@sha256:<digest> \
  --certificate-identity https://github.com/gyanendra0/DENIAL-APPEAL-AGENT/.github/workflows/images.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Running it on Kubernetes

`deploy/k8s/` describes the whole app: `base/` plus the overlays `local` (a throwaway cluster,
made-up values) and `prod` (the server, by image digest). How and why: architecture
[3.26](docs/03-architecture.md#326-running-on-kubernetes).

To see what an overlay produces, without a cluster:
```
kubectl kustomize deploy/k8s/overlays/local
kubectl kustomize deploy/k8s/overlays/prod
```

To start `local` in a cluster on your own machine, follow the steps of the job `smoke` in
`.github/workflows/checks.yml`, with [kind](https://kind.sigs.k8s.io/) installed. They are the
tested version, so they are not copied here.

Two things are not in these files, and the API does not start without them: the Secret
`app-secrets` (keys in `deploy/k8s/overlays/prod/app-secrets.example.yaml`) and the model:
```
kubectl -n denial-appeal-agent create configmap win-model --from-file=models/
```

A pod reads its settings and the model once, at start, so after changing `app-config`,
`app-secrets` or `win-model`, restart it:
```
kubectl -n denial-appeal-agent rollout restart deployment/api
```

### The server

Production is one Oracle A1 machine with k3s, Sealed Secrets and Argo CD, pinned in
`deploy/k8s/addons/`. Argo CD keeps it equal to `deploy/k8s/overlays/prod` on `main`, so a merged
deploy pull request (see "Publishing the images") is a deploy. The machine, the first start in
order, and what was measured are in architecture
[3.27](docs/03-architecture.md#327-running-on-the-server). The first start has a fixed order:
namespace and `win-model`, then the sync, then the evidence tables. To rebuild it from nothing,
follow the [setup guide](docs/07-setup-guide.md).

- **Secrets** are in `deploy/k8s/overlays/prod/app-secrets.sealed.yaml`, which only this server's
  controller can open. To change one value, fill a copy of `app-secrets.example.yaml` outside the
  repository and seal the whole file again with `kubeseal`.
- **A failed migration** stops the sync before the API is replaced, and Argo CD retries the sync
  by itself. Fix the cause and the next retry goes through.
- **Argo CD looks at `main` every 3 minutes.** To make it fetch at once:
  `kubectl -n argocd annotate application denial-appeal-agent argocd.argoproj.io/refresh=normal --overwrite`.

### Backups and alerts

Every night at 03:00 UTC the CronJob `backup` dumps the database, encrypts it to the gpg public key
in `deploy/k8s/base/backup/` and uploads it to an Oracle Object Storage bucket, which deletes each
dump after 30 days. The server's bucket key can add a dump but not read, overwrite or delete one,
and the private key that opens a dump is never on the server. UptimeRobot checks `/health` every
5 minutes and emails when it fails. How and why, and what was measured: architecture
[3.28](docs/03-architecture.md#328-backups-and-alerts).

- **Run a backup now** (only if no dump exists yet for that UTC date: a second one is refused as an
  overwrite, and a hand run before 03:00 UTC makes that night's run fail):
  `kubectl -n denial-appeal-agent create job --from=cronjob/backup backup-now`.
- **Last runs:** `kubectl -n denial-appeal-agent get jobs`.
- **Restore** a downloaded dump into a new database with `deploy/k8s/base/backup/restore.sh`, run
  in the pinned db image (it has `gpg`) with `docker run -it`, since gpg asks for the key's
  passphrase on the terminal; it never writes over an existing database. Compare
  `row-counts.sql` on both sides. The job `backup-restore` in `.github/workflows/checks.yml` is the
  tested example.
- **Monthly check** (on the 1st): Oracle Cost analysis, last month's `sum(cost_usd)` in
  `llm_calls`, and the bucket's size and newest dump.

## Who does what

| Where | What happens there |
|---|---|
| The development Mac | Code and docs are written and checked (lint, types, tests, the local database and the dev server). Commits are made here; the owner pushes, opens and merges pull requests, and creates the release tags |
| GitHub | The required checks run on every pull request; a merge to `main` builds, signs and publishes the images and opens the deploy pull request |
| The server | Argo CD applies `main` to k3s; the nightly backup runs there. Nothing is copied to it by hand except what the [setup guide](docs/07-setup-guide.md) says (the add-ons, the model files, the evidence tables) |

Unless a section says otherwise, a command in these documents runs on the development
machine, from the repository's top folder.

## What gets committed

Source code, published documentation, configuration templates and deployment manifests.

**Never committed:** secrets, datasets, trained models, dependency folders, and **working
documents** - task sheets, checklists and scratch notes live in `docs/working/` and stay
local. See `.gitignore`.

All work follows the same workflow: no direct commits to `main`, an issue before every
branch, one branch per feature group, pull request with green checks, squash merge.

> **Note on sources.** The loaded sources were inspected before use, and the numbers in these
> documents were measured. The candidate sources that were never loaded
> ([data 2.2](docs/02-data.md#22-layer-a--claims-and-denial-outcomes)) were not checked:
> verify one before depending on it.
