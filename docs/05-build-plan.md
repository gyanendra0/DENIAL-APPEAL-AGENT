# 5. Build Plan

Seven stages, ordered by dependency. Each stage produces working code and ends with a
condition that must hold on the run machine before the next stage begins.

Stages are not dated. Do not reorder them — each one relies on the previous.

Every stage is delivered as 2–4 feature-group branches, one issue and one pull request
each. A stage is complete when its done
condition holds and a release tag is pushed.

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

---

## Stage 2 — Documents

**Goal:** realistic denial documents to work on.

**Produces:**

- A denial-letter generator with several templates, driven by loaded claim rows.
- Clinical note and prior-authorisation generators.
- Noise injection: OCR-style errors, missing fields, layout variation.
- Seed and generator version stored with every document.

**Done when:** every claim has at least one document, and any document can be regenerated
identically from its seed.

**Tag:** `v0.3.0`

---

## Stage 3 — Extraction and prediction

**Goal:** the system understands and scores a denial.

**Produces:**

- LLM gateway: one entry point, primary provider, fallback provider, monthly budget cap.
- Schema-bound extraction returning typed fields with a confidence score per field.
- Feature engineering and a gradient-boosted win-probability model.
- Deterministic rules: appeal deadlines, amount floors, must-review conditions.
- An evaluation harness reporting the metrics from
  [01-problem-and-scope.md](01-problem-and-scope.md).

**Done when:** on the test split, field extraction accuracy is at least 85% and model AUC
is at least 0.70.

**Tag:** `v0.4.0`

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

---

## Stage 5 — Product

**Goal:** people can use it without a terminal.

**Produces:**

- Sign-up, login, accounts and roles, with per-account isolation enforced in the API.
- Upload endpoint with background job status.
- Screens: worklist, split-view denial detail, draft review, overview dashboard.
- Audit log and LLM cost tracking.

**Done when:** a new user can sign up, upload a denial, and approve a draft entirely in
the browser.

**Tag:** `v0.6.0`

---

## Stage 6 — Deployment

**Goal:** it runs continuously.

**Produces:**

- Container definitions for the API and frontend.
- Deployment manifests for a single small always-on machine.
- Backup, health-check and alerting configuration.
- A setup guide detailed enough to rebuild the environment from nothing.

**Done when:** the application is served continuously and monthly cost is confirmed under
$5.

**Tag:** `v1.0.0`

---

## Metrics

Measured on the run machine and recorded in the release notes for each tag.

| Metric | Target | First measured |
|---|---|---|
| Test coverage on `src/` | ≥ 70% | Stage 0, every stage after |
| Extraction field accuracy | ≥ 85% | Stage 3 |
| Win-model AUC | ≥ 0.70 | Stage 3 |
| Draft acceptance rate | ≥ 60% | Stage 4 |
| Upload to draft ready | < 2 minutes | Stage 5 |
| Monthly running cost | < $5 | Stage 6 |

---

## Risks by stage

| Stage | Risk | Response |
|---|---|---|
| 1 | A public source is unavailable or has changed | Switch to the next source in `02-data.md` and record the change there |
| 2 | Generated documents are too easy to extract from | Add templates and noise until accuracy drops to a believable level |
| 3 | LLM cost rises | Use a smaller model, cache repeated prompts, batch requests |
| 4 | Drafts contain invented policy text | Tighten the citation validator — block, never warn |
| 5 | Interface scope grows | Ship the four core screens first; everything else waits |
| 6 | Free hosting is withdrawn | Keep the setup guide complete enough to rebuild elsewhere in a day |
