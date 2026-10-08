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
| `llm` | One gateway for all model calls (see 3.8) | Primary API, open-weight fallback, budget guard |
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

Two kinds of table hold no customer data and have no `account_id`: public reference tables
loaded from public sources, and operational tables such as `llm_calls` (see 3.8). The rule
is in [conventions 6.4](06-conventions.md#64-database).

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

## 3.8 LLM gateway

Every model call in the project goes through one method, `LlmGateway.complete(request)`.
Nothing else talks to a model provider. The gateway checks the monthly budget before a
call, uses a fallback provider when the primary cannot be used, and records what each
answered call cost. Over budget means fallback or refuse, never overspend.

The gateway returns text. Parsing that text into a typed schema is the caller's job.

No real model call has been made yet: the gateway is tested with stub providers only. The
first real call belongs to the extraction work that follows.

### Where the code is

| File | Holds |
|---|---|
| `src/llm/gateway.py` | `LlmRequest`, `LlmResult`, the `ChatProvider` interface, `LlmGateway` and its errors |
| `src/llm/openai_provider.py` | `OpenAiChatProvider`, the one provider class, and `build_openai_gateway(settings, session_factory)` |
| `src/llm/budget.py` | The budget guard: `call_cost`, `month_to_date_spend`, `check_budget`, `record_llm_call` |

The primary and the fallback are the same class. Both are services that speak the OpenAI
chat format, so they differ only in base URL, model, key and prices, and all of those come
from settings. Another host can be used without a code change.

A request carries the system text, the user text, `max_tokens`, a prompt version and a
purpose. A result carries the text, the provider that answered (`primary` or `fallback`),
the model name, the prompt version, the input and output token counts, the cost and the
stop reason. Both are Pydantic models.

### One call, step by step

1. **Budget check for the primary.** The highest possible cost of the call is added to
   this month's spend. If the sum is at or below the cap, the call is allowed.
2. **Call the primary.** If it answers, go to step 5.
3. **Budget check for the fallback.** Reached when the primary did not fit the budget or
   was unavailable. The fallback has its own prices, so it gets its own check. A fallback
   priced at 0 adds nothing to the spend.
4. **Call the fallback.**
5. **Record the spend.** One row is written to `llm_calls`.
6. **Log one line** and return the result.

| Situation | What happens |
|---|---|
| Primary fits the budget and answers | Result from the primary, one row written |
| Primary does not fit the budget | The primary is not called; the fallback is tried |
| Primary hits a rate limit, a connection error, a timeout or a server error | The fallback is tried |
| Primary rejects the request (a bad request, a wrong key) | The error is raised as it is; the fallback is not called |
| The primary does not fit the budget and no fallback is configured | `LlmBudgetExceededError`; no provider is called, no row written |
| The fallback is needed but does not fit the budget | `LlmBudgetExceededError`; the fallback is not called, no row written |
| The primary is unavailable and no fallback is configured, or the fallback is unavailable too | `LlmUnavailableError`, no row written |

A bad request or a wrong key is a bug or a setup mistake. A second provider would not fix
it, so it is not hidden behind a fallback.

Providers signal a failure that another provider may cover with
`ProviderUnavailableError`. It carries the error's class name only, because the SDK's own
message may quote the request.

### Cost and the budget

- **Before a call**, the gateway needs an amount the call can never exceed. There is no
  free token counter, so the highest possible input is the UTF-8 byte length of the prompt
  (a token is never smaller than one byte) plus 128 tokens for message framing. The
  highest possible output is the request's `max_tokens`.
- **After a call**, the cost is the token counts the provider reported times the prices
  from settings. If the provider reports no counts, the highest possible counts are
  recorded, because the call was answered and paid for.
- Cost is an exact decimal, rounded up to a millionth of a dollar. Never a float.
- The budget is `LLM_MONTHLY_BUDGET_USD`. A month is a calendar month in UTC. A call whose
  highest possible cost brings the spend exactly to the cap is allowed.

### The spend table

`llm_calls` has one row per answered call: `provider` (`primary` or `fallback`),
`model_name`, `prompt_version`, `purpose`, `input_tokens`, `output_tokens`, `cost_usd`
(six decimal places), `created_at` and `updated_at`.

- `provider` is the role that answered, not the host. The host behind each role is a
  setting.
- The table is operational. It has no `account_id` and never holds a prompt, a document
  text, an answer or a personal field (see [conventions 6.4](06-conventions.md#64-database)).
- The row is written in its own transaction. If the caller later rolls back its own work,
  the spend row stays, because the money was spent.
- The cap survives a restart, because the spend is read from this table.

### Logging

One line per answered call:

```text
llm call: provider=primary model=<model> purpose=<purpose> input_tokens=812 output_tokens=140 cost_usd=0.000206 fallback_used=False
```

A log line never holds the prompt, document text, the answer or a key.

### Settings

The keys are read only when a gateway is built. Importing the project, running the data
and document pipelines and running the tests need none of them. A missing required key
fails when the gateway settings are loaded, with a message that names the key.
`.env.example` lists every key with a placeholder value.

| Key | Required | Meaning |
|---|---|---|
| `LLM_MONTHLY_BUDGET_USD` | yes | Most the gateway may spend in one calendar month (UTC), in US dollars |
| `LLM_PRIMARY_BASE_URL` | yes | Base URL of the primary provider (`http://` or `https://`) |
| `LLM_PRIMARY_MODEL` | yes | Model the primary provider calls |
| `LLM_PRIMARY_API_KEY` | yes | Key for the primary provider; a secret, never logged |
| `LLM_PRIMARY_INPUT_USD_PER_MTOK` | yes | Price of one million input tokens, in US dollars |
| `LLM_PRIMARY_OUTPUT_USD_PER_MTOK` | yes | Price of one million output tokens, in US dollars |
| `LLM_FALLBACK_BASE_URL` | no | Base URL of the fallback provider |
| `LLM_FALLBACK_MODEL` | no | Model the fallback provider calls |
| `LLM_FALLBACK_API_KEY` | no | Key for the fallback provider, if it needs one |
| `LLM_FALLBACK_INPUT_USD_PER_MTOK` | no | Price of one million input tokens; 0 when not set |
| `LLM_FALLBACK_OUTPUT_USD_PER_MTOK` | no | Price of one million output tokens; 0 when not set |

`LLM_FALLBACK_BASE_URL` and `LLM_FALLBACK_MODEL` are set together, or both left empty for
no fallback. A fallback key without a fallback base URL is rejected. Amounts are exact
decimals of 0 or more.

### Known limits

- The 128-token framing margin is a guess. It has not been compared with the token counts
  a real provider reports.
- A call that fails after the provider has already produced an answer (a timeout while
  the answer travels back) may be billed but is not recorded.
- Two processes that check the budget at the same moment can both be allowed. One pipeline
  runs at a time today; concurrent use needs a lock first.
- The prices are settings typed in by hand. If a provider changes its prices, the
  recorded cost is wrong until the settings are updated.
