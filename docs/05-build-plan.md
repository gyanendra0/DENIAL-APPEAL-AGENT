# 5. Build Plan

Seven stages, ordered by dependency. Each stage produces working code and ends with a
condition that must hold before the next stage begins.

**All seven stages are done.** The last one was released as `v1.0.0` on 2026-11-04. Each
stage below keeps its plan and adds its **Result**: the tag date and what was measured. The
plan has no Stage 7; the open limits of v1.0.0 are in
[03-architecture.md, "Known limits of v1.0.0"](03-architecture.md#known-limits-of-v100).

Stages were not dated in advance, and none was reordered: each one relies on the previous.

Each stage was planned as 2–4 feature-group branches, with one issue and one pull request
each. A stage is complete when its done condition holds and a release tag is pushed. The
real number of pull requests per stage:

| Stage | Pull requests |
|---|---|
| 0 | 3 |
| 1 | 6 |
| 2 | 4 |
| 3 | 6 |
| 4 | 10 (the draft acceptance took five measurements) |
| 5 | 5 |
| 6 | 18, plus 1 Dependabot update and 5 bot deploy pull requests |

---

## Stage 0 — Foundation

**Goal:** a skeleton every later stage plugs into.

**Produces:**

- `pyproject.toml` with dependencies and tool configuration (ruff, black, mypy, pytest).
- A settings module that reads configuration from the environment and validates it.
- Database engine, session handling and a base model with `id`, `created_at`,
  `updated_at`.
- First migration: `accounts`, `users`, `claims`, `denials`.
- Test fixtures using a throwaway database.
- A CI workflow running lint, format check, type check and tests with coverage.
- `.env.example` listing every configuration key with placeholder values.

**Done when:** a fresh clone installs, migrates up and down cleanly, and passes CI.

**Tag:** `v0.1.0`

**Result (2026-09-29):** met. A fresh clone installs, migrates up and down, and CI is green.

---

## Stage 1 — Data in

**Goal:** real public data in the database.

**Produces:**

- One loader per public source listed in [02-data.md](02-data.md).
- Cleaning and joining logic.
- Quality gates that reject a bad batch outright.
- The labelling rule set, versioned, with the version stored on every row.
- Split logic — train / validation / test, split by claim, never by document.
- A pipeline script that runs the whole sequence end to end.

**Done when:** one command cleans, validates and loads the downloaded source files, and
the quality report passes. The source files are downloaded once by hand; the links are in
[02-data.md, section 2.9](02-data.md#29-running-the-whole-pipeline).

**Tag:** `v0.2.0`

**Result (2026-10-06):** met. One command (`pipelines/run_data_pipeline.py`) loads two
sources: the Marketplace Transparency in Coverage PUF (real issuer and plan counts) and the
CMS DE-SynPUF carrier claims (synthetic). On the first 50,000 claims, 5,325 are denied and
the quality report passes. The appeal label is a proxy from a fixed rule, not an observed
outcome.

---

## Stage 2 — Documents

**Goal:** realistic denial documents to work on.

**Produces:**

- A denial-letter generator with several templates, driven by loaded claim rows.
- Clinical note and prior-authorisation generators.
- Noise injection: OCR-style errors, missing fields, layout variation.
- Seed and generator version stored with every document.

**Done when:** every denied claim has at least one document, and any document can be
regenerated identically from its seed. Claims that are not denied get no document; the rule
is in [02-data.md, section 2.4.1](02-data.md#241-generated-denial-letters-v1).

**Tag:** `v0.3.0`

**Result (2026-10-07):** met. 13,067 documents (5,325 letters, 5,325 clinical notes, 2,417
prior-authorisation records) cover every denied claim, and are rebuilt identically from
seed 42.

---

## Stage 3 — Extraction and prediction

**Goal:** the system understands and scores a denial.

**Produces:**

- LLM gateway: one entry point, primary provider, fallback provider, monthly budget cap.
  As built: `gpt-4o-mini` (and `text-embedding-3-small`) as primary, an open-weight model
  through an OpenAI-compatible service as fallback.
- Schema-bound extraction returning typed fields with a confidence score per field.
- Feature engineering and a gradient-boosted win-probability model.
- Deterministic rules: appeal deadlines, amount floors, must-review conditions.
- An evaluation harness reporting the metrics from
  [01-problem-and-scope.md](01-problem-and-scope.md).

**Done when:** on the test split, field extraction accuracy is at least 85% and model AUC
is at least 0.70.

**Tag:** `v0.4.0`

**Result (2026-10-10):** met. Field accuracy 93.04%, AUC 0.7539. The label rule allows at
most 0.7522 on the test split, so the AUC shows the model learned the rule, not that it
predicts real appeals.

---

## Stage 4 — Evidence and drafting

**Goal:** an appeal letter a reviewer would actually send.

**Produces:**

- Chunking and embedding of the policy corpus into pgvector.
- Retrieval with ranking and filtering.
- Letter generation in which every policy statement carries a citation.
- A citation validator that **blocks** any draft whose citation does not match
  retrieved text.
- The agent loop: extraction → rules → scoring → retrieval → drafting, with a full trace
  stored, stopping for human review.

**Done when:** a reviewer accepts at least 60% of drafts with only light edits.

**Tag:** `v0.5.0`

**Result (2026-10-16):** met at 23 of 30 drafts = 76.67%, in the fifth measurement. **The
drafts were scored by a model against a written rubric, not by a person** (the owner's
decision of 2026-10-11). The four earlier measurements gave 0%, 0%, 56.67% and 66.67%.

---

## Stage 5 — Product

**Goal:** people can use it without a terminal.

**Produces:**

- Sign-up, login, accounts and roles, with per-account isolation enforced in the API.
- Upload endpoint with background job status.
- Screens: worklist, split-view denial detail, draft review, overview dashboard. As built,
  also sign-up, login and upload screens; see
  [04-product-and-dashboard.md](04-product-and-dashboard.md).
- Audit log and LLM cost tracking.

**Done when:** a new user can sign up, upload a denial, and approve a draft entirely in
the browser.

**Tag:** `v0.6.0`

**Result (2026-10-20):** met. One browser run by a new user: sign-up, upload, approve. Upload
to draft ready took 12.0 s.

---

## Stage 6 — Deployment

**Goal:** it runs continuously.

**Produces (planned):**

- Container definitions for the API and frontend.
- Deployment manifests for a single small always-on machine.
- Backup, health-check and alerting configuration.
- A setup guide detailed enough to rebuild the environment from nothing.

**Produced (as built):**

- An API image and a Caddy web image, with Docker Compose (`deploy/prod/compose.yml`) kept as
  the fallback.
- Kubernetes manifests (`deploy/k8s/`) run by k3s on one Oracle Always Free A1 machine. Argo
  CD keeps the server matched to `main`, and secrets are committed only in sealed form
  (Sealed Secrets).
- An image pipeline: each merge builds, scans, signs and publishes the images, and a bot deploy
  pull request moves the server to them.
- Security gates: 9 required checks on `main` (`checks`, `zizmor`, `frontend`, `trivy`,
  `lock-tests`, `image-scan`, `backup-restore`, `manifests`, `smoke`) and Dependabot.
- A nightly encrypted `pg_dump` to OCI Object Storage with a restore test, and an UptimeRobot
  check on `/health` that sends an email.
- The setup guide [07-setup-guide.md](07-setup-guide.md), with its risky parts rehearsed.
- Account features added in this stage: login limit, change and reset password, email code,
  Google sign-in, the shared account and personal accounts.

**Done when:** the application is served continuously and monthly cost is confirmed under
$5.

**Tag:** `v1.0.0`

**Result (2026-11-04):** met. Served at `https://denial-appeal.duckdns.org` since
2026-11-02, uptime 99.783%. November so far: Oracle $0.00 and model calls $0.001617.

---

## Metrics

Measured and recorded in the release notes for each tag.

| Metric | Target | First measured | Measured |
|---|---|---|---|
| Test coverage on `src/` | ≥ 70% | Stage 0, every stage after | 99.87% at v1.0.0 (4,132 tests); frontend 99.79% of lines (479 tests) |
| Extraction field accuracy | ≥ 85% | Stage 3 | 93.04% |
| Win-model AUC | ≥ 0.70 | Stage 3 | 0.7539 (best possible 0.7522 for the label rule) |
| Draft acceptance rate | ≥ 60% | Stage 4 | 76.67%, scored by a model |
| Upload to draft ready | < 2 minutes | Stage 5 | 12.0 s |
| Monthly running cost | < $5 | Stage 6 | Oracle $0.00 + model $0.001617 (November so far) |

---

## Risks by stage

| Stage | Risk | Response | What happened |
|---|---|---|---|
| 1 | A public source is unavailable or has changed | Switch to the next source in `02-data.md` and record the change there | Both sources were available. Each file was inspected before its loader was written |
| 2 | Generated documents are too easy to extract from | Add templates and noise until accuracy drops to a believable level | Seeded noise (`noise v1`) was added; extraction still reached 93.04% |
| 3 | LLM cost rises | Use a smaller model, cache repeated prompts, batch requests | `gpt-4o-mini` with a hard monthly cap. No cache was needed: the whole of Stages 3–5 cost under $1 |
| 4 | Drafts contain invented policy text | Tighten the citation validator — block, never warn | The validator blocks; a refused draft is never stored. It also blocked every `medical_necessity` upload in the Stage 5 measurements |
| 5 | Interface scope grows | Ship the four core screens first; everything else waits | Stage 5 shipped the core screens in two pull requests; the account features and the polish waited for Stage 6 |
| 6 | Free hosting is withdrawn | Keep the setup guide complete enough to rebuild elsewhere in a day | Guide 07 covers Oracle k3s and the Compose fallback. Oracle may still reclaim an idle machine; the uptime alert would report it |
