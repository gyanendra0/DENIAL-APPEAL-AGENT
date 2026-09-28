# 3. Architecture

## 3.1 One picture

```text
                 ┌──────────────────────────┐
  Browser  ─────▶│  React dashboard (Vite)  │
                 └────────────┬─────────────┘
                              │ HTTPS + JWT
                 ┌────────────▼─────────────┐
                 │      FastAPI backend     │
                 │  auth · uploads · jobs   │
                 └────────────┬─────────────┘
                              │
        ┌──────────┬──────────┼──────────┬──────────────┐
        ▼          ▼          ▼          ▼              ▼
   extraction    ml        rules       rag           agent
   (GenAI)    (win model) (policy)  (evidence)   (orchestrates)
        │          │          │          │              │
        └──────────┴──────────┴────┬─────┴──────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ PostgreSQL + pgvector        │
                    │ claims · denials · drafts    │
                    │ evidence chunks · audit log  │
                    └──────────────────────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │ Object storage (uploads)     │
                    └──────────────────────────────┘
```

## 3.2 Components

| Component | Job | Key choice |
|---|---|---|
| `ingest` | Take files in, normalise, queue work | Accept PDF, image, text |
| `extraction` | Document → structured fields | LLM with a strict schema; OCR when needed |
| `ml` | Predict appeal success | Gradient boosting first; a neural net only if it beats it |
| `rules` | Hard policy gates: deadlines, amount floors, must-review cases | Deterministic, testable, no LLM |
| `rag` | Find and rank supporting evidence | pgvector, chunked policy corpus |
| `agent` | Run the loop, call tools, stop and ask a human | Bounded steps, full trace logged |
| `llm` | One gateway for all model calls | Primary API, open-weight fallback, budget guard |
| `api` | HTTP surface, auth, per-account isolation | FastAPI, JWT, role checks |
| `db` | Schema and migrations | SQLAlchemy + Alembic |
| `synth` | Generate Layer C documents | Seeded and reproducible |

## 3.3 Request flow

1. User uploads a denial. File goes to object storage, a row goes to `denials`.
2. A background job runs extraction; fields are saved with a confidence score.
3. Rules run. A hard fail (for example, the appeal window has closed) stops here.
4. The ML model scores the appeal. Score plus expected value is stored.
5. If the score clears the threshold, the agent retrieves evidence and drafts a letter.
6. The draft appears on the dashboard as **Needs review**.
7. A human edits, approves or rejects. Every action is written to the audit log.

Everything after step 1 is asynchronous. The UI polls job status.

## 3.4 Data model (core tables)

| Table | Holds |
|---|---|
| `accounts` | Tenants |
| `users` | Login, role, account link |
| `claims` | Claim header, payer, amounts |
| `denials` | Denial record, reason codes, source file |
| `extractions` | Fields pulled from a document, with confidence and model version |
| `predictions` | Score, model version, features used |
| `evidence_chunks` | Policy text plus embedding |
| `drafts` | Generated letter, citations, status |
| `reviews` | Who approved or rejected, when, why |
| `audit_log` | Every automated decision, immutable |

Every business table carries `account_id`, and every query filters on it. This is the
single most important rule in the codebase.

## 3.5 Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Ecosystem for ML and LLMs |
| API | FastAPI + Pydantic | Typed, fast, auto docs |
| ORM / migrations | SQLAlchemy 2 + Alembic | Standard, testable |
| Database | PostgreSQL + pgvector | One store for rows *and* vectors — keeps cost near zero |
| Jobs | Postgres-backed queue | No extra broker to pay for or run |
| ML | scikit-learn, XGBoost/LightGBM | Strong on tabular, cheap to train |
| LLM | Hosted API primary, open-weight fallback | Quality with a cost escape hatch |
| Frontend | React + Vite + TypeScript | Fast build, typed |
| Tests | pytest, coverage | Standard, cheap |
| Lint/format | ruff, black, mypy | One command |
| Checks | Automated check script | Lint, types and tests in one step |
| Runtime | Containers on one small always-on machine | Under budget |

## 3.6 Folder layout

```text
denial-appeal-agent/
├─ src/
│  ├─ agent/        orchestration loop and tools
│  ├─ api/          FastAPI routes, auth, schemas
│  ├─ db/           models, migrations, session
│  ├─ extraction/   document → fields
│  ├─ ingest/       loaders, file handling
│  ├─ llm/          provider gateway, prompts, budget guard
│  ├─ ml/           features, training, inference
│  ├─ rag/          chunking, embedding, retrieval
│  ├─ rules/        deterministic policy checks
│  └─ synth/        synthetic document generation
├─ tests/           mirrors src/
├─ pipelines/       runnable end-to-end scripts
├─ frontend/        React dashboard
├─ config/          settings, prompt and rule config
├─ deploy/          container and deployment manifests
└─ docs/            these documents
```

## 3.7 Non-functional targets

| Concern | Target |
|---|---|
| Upload to draft ready | < 2 minutes |
| API response (non-job) | < 300 ms at p95 |
| Test coverage on `src/` | ≥ 70% |
| Cost | < $5/month |
| Secrets | Environment only, never stored in the project |
| Auditability | Every automated decision reconstructable from the log |
