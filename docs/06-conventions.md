# 6. Coding Conventions

Rules every file in this repository follows. This document covers the code itself.

---

## 6.1 Naming

| Thing | Convention | Example |
|---|---|---|
| Modules, functions, variables | `snake_case` | `extract_denial_reason` |
| Classes | `PascalCase` | `DenialExtractor` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_UPLOAD_BYTES` |
| Database tables | plural `snake_case` | `evidence_chunks` |
| Database columns | singular `snake_case` | `account_id` |
| Test files | `test_<module>.py` | `test_extractor.py` |
| Test functions | `test_<behaviour>` | `test_rejects_upload_over_size_limit` |
| Pipeline scripts | `<verb>_<noun>.py` | `load_claims.py` |
| Environment variables | `UPPER_SNAKE_CASE` | `LLM_MONTHLY_BUDGET_USD` |

---

## 6.2 Project structure

```text
src/
  agent/        orchestration loop and tools
  api/          FastAPI routes, auth, request/response schemas
  config/       settings
  db/           models, migrations, session
  extraction/   document → structured fields
  ingest/       loaders and file handling
  llm/          provider gateway, prompts, budget guard
  ml/           features, training, inference
  rag/          chunking, embedding, retrieval
  rules/        deterministic policy checks
  synth/        synthetic document generation
tests/          mirrors src/ exactly
pipelines/      runnable end-to-end scripts
frontend/       React dashboard
deploy/         container and deployment manifests
```

Rules:

- `tests/` mirrors `src/`. `src/rag/retriever.py` is tested in
  `tests/rag/test_retriever.py`.
- One responsibility per module. If the filename needs "and", split it.
- Component folders do not reach into each other's internals. They call a small public
  function, or they do not call at all.
- Anything runnable end to end belongs in `pipelines/`, not `src/`.

---

## 6.3 Python

- Python 3.11 or later.
- Type hints on every function signature.
- Pydantic models for everything that crosses a boundary: API bodies, LLM output,
  configuration, loader output.
- No bare `except:`. Catch the specific exception, or let it propagate.
- No hard-coded paths, URLs, model names or credentials. All of it comes from settings.
- Log at boundaries — requests, jobs, external calls — not inside tight loops.
- Never log secrets, document contents or personal fields.
- A function that spends money or takes more than a second says so in its docstring.
- Formatting by `black`, linting by `ruff`, types checked by `mypy`. CI enforces all
  three.

---

## 6.4 Database

- Every business table has `account_id`: indexed, non-null, foreign key to `accounts`.
- **No query on a business table without an `account_id` filter.** This is the single
  most important rule in the codebase — breaking it leaks one customer's data to another.
- Every table has `created_at` and `updated_at`.
- Money uses an exact decimal type, never a float.
- Enumerated values (roles, statuses) use constrained types, not free text.
- One migration per pull request. Every migration has a working downgrade.
- Read the generated SQL before accepting a migration.

---

## 6.5 LLM usage

- Every model call goes through the `llm` gateway. Nothing calls a provider directly.
- Every call passes the budget check first. Over budget means fallback or refuse, never
  overspend.
- Prompts live in `config/prompts/`, are versioned, and contain no secrets.
- Model output is parsed into a Pydantic schema. Output that fails to parse is an error,
  not a best-effort guess.
- The model name and prompt version are stored with every result.
- Any policy statement in generated text must cite retrieved evidence, or the output is
  rejected.

---

## 6.6 Testing

- Every public function has at least one test.
- Tests are written in the same pull request as the code they test.
- Tests use small fixture files stored beside them. No live downloads, no real API
  calls, no real LLM calls.
- External services are replaced with stubs: LLM providers, HTTP sources, file storage.
- Database tests use a throwaway schema and leave nothing behind.
- Coverage on `src/` must stay at or above 70%. CI fails below that.
- Test names describe the behaviour, not the function: `test_rejects_expired_appeal`,
  not `test_check_deadline`.

---

## 6.7 Configuration and secrets

- All configuration is read from environment variables through the settings module.
- `.env.example` lists every key with a placeholder value. It is the only environment
  file committed.
- A missing required setting fails at startup with a message naming the key — never
  later, never silently.
- Secrets never appear in code, tests, logs, prompts, commit messages or pull request
  descriptions.

---

## 6.8 Hard rules

These are never broken. Each one exists because breaking it causes real harm.

| Rule | Harm prevented |
|---|---|
| No business-table query without `account_id` | One customer sees another's data |
| No LLM call without a budget check | An unbounded bill |
| No draft released without a valid citation for every policy statement | Invented policy sent to an insurer |
| No secret in the repository | Account compromise |
| No real patient data, ever | Privacy and legal exposure |
| Nothing sent to an insurer automatically | Unreviewed legal documents sent in someone's name |
