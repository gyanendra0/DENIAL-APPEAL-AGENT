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
| `extraction` | Document → structured fields (see 3.9) | LLM answer checked against a typed schema; OCR when needed |
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

The business table `extractions` does not exist yet: it is built when customer uploads
exist. Until then, what a model reads from the generated documents is stored in the public
reference table `document_extractions` (see 3.9).

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

The tests use stub providers only and make no real call. The first real calls were made by
the extraction work (see 3.9).

### Where the code is

| File | Holds |
|---|---|
| `src/llm/gateway.py` | `LlmRequest`, `LlmResult`, the `ChatProvider` interface, `LlmGateway` and its errors |
| `src/llm/openai_provider.py` | `OpenAiChatProvider`, the one provider class, and `build_openai_gateway(settings, session_factory)` |
| `src/llm/budget.py` | The budget guard: `call_cost`, `month_to_date_spend`, `check_budget`, `record_llm_call` |

The primary and the fallback are the same class. Both are services that speak the OpenAI
chat format, so they differ only in base URL, model, key and prices, and all of those come
from settings. Another host can be used without a code change.

A request carries the system text, the user text, `max_tokens`, a prompt version, a
purpose and, if the caller wants one, a response schema (see below). A result carries the text, the provider that answered (`primary` or `fallback`),
the model name, the prompt version, the input and output token counts, the cost and the
stop reason. Both are Pydantic models.

### Response schema

A request may carry a JSON schema in `response_schema`. The provider sends it to the
service as `response_format`, so the model is asked to answer in that shape. A request
without one is sent as before.

The schema is sent in non-strict mode: the service aims for the shape but does not promise
it. Strict mode was tried with one real call to each service on 2026-10-09 and the
fallback service refused the extraction schemas, so strict mode is off for both. The
caller must therefore still check the answer itself.

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
| Primary hits a rate limit, a connection error, a timeout, a server error, or answers with status 408 or 409 | The fallback is tried |
| A provider rejects the request (a bad request, a wrong key) | `ProviderRejectedError`; the fallback is not called, no row written |
| The primary does not fit the budget and no fallback is configured | `LlmBudgetExceededError`; no provider is called, no row written |
| The fallback is needed but does not fit the budget | `LlmBudgetExceededError`; the fallback is not called, no row written |
| The primary is unavailable and no fallback is configured, or the fallback is unavailable too | `LlmUnavailableError`, no row written |

A bad request or a wrong key is a bug or a setup mistake. A second provider would not fix
it, so it is not hidden behind a fallback.

Providers signal a failure that another provider may cover with
`ProviderUnavailableError`. It carries the error's class name only, because the SDK's own
message may quote the request.

A rejected request is raised as `ProviderRejectedError`. It carries the error's class name
and the status code only, and the SDK's error is not attached to it: the SDK's message
holds the service's whole response body, which may quote the request or a failed answer,
and a traceback would print it.

The SDK's own retries are switched off. A retry inside the SDK would be a second paid
request under one budget check and one spend row. The fallback is the retry.

### Cost and the budget

- **Before a call**, the gateway needs an amount the call can never exceed. There is no
  free token counter, so the highest possible input is the UTF-8 byte length of the prompt
  (a token is never smaller than one byte) plus 128 tokens for message framing. A response
  schema is billed as input too, so its bytes are added. The highest possible output is
  the request's `max_tokens`.
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

If the provider answered but the spend row could not be stored, one error line with the
same fields is logged (`llm call answered but not recorded: ...`) and the error is raised.
The answer is not returned.

A log line never holds the prompt, document text, the answer or a key. Printing a request
or a result leaves the prompt and the answer out too.

### Settings

The keys are read only when a gateway is built. Importing the project, running the data
and document pipelines and running the tests need none of them. A missing required key
fails when the gateway settings are loaded, with a message that names the key.
`.env.example` lists every key with a placeholder value.

| Key | Required | Meaning |
|---|---|---|
| `LLM_MONTHLY_BUDGET_USD` | yes | Most the gateway may spend in one calendar month (UTC), in US dollars |
| `LLM_TIMEOUT_SECONDS` | yes | Longest one call may take, for both providers; above 0 |
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

A provider that does not answer within `LLM_TIMEOUT_SECONDS` is cut off. A timeout counts
as unavailable, so the fallback is tried.

### Known limits

- The highest possible input is safe but loose. On the real calls of 2026-10-09 it was
  3.5 to 4.2 times the input the providers reported, and about four times with a response
  schema. This only matters when the month's spend is within one call of the cap: a call
  that would have fitted is then refused.
- A call that fails after the provider has already produced an answer (a timeout while
  the answer travels back) may be billed but is not recorded.
- An answered call whose spend row cannot be stored (the database is down) is logged but
  not counted against the budget.
- Two processes that check the budget at the same moment can both be allowed. One pipeline
  runs at a time today; concurrent use needs a lock first.
- The prices are settings typed in by hand. If a provider changes its prices, the
  recorded cost is wrong until the settings are updated.

## 3.9 Extraction

Extraction turns the text of one document into typed fields. Each field comes back as a
value and a confidence from 0 to 1. The model is called once per document, through the
gateway (3.8). An answer that does not fit the schema is an error, never a best-effort
guess.

Today the input is the stored text of a generated document
([02-data.md 2.4](02-data.md#24-layer-c--generated-documents)). The document type is
stored with the document and is given to the extractor; it is not guessed. OCR, PDFs and
customer uploads are not built yet.

### Where the code is

| File | Holds |
|---|---|
| `src/extraction/schemas.py` | The three schemas, `schema_for(document_type)` and `parse_extraction(document_type, answer)` |
| `src/extraction/prompts.py` | `load_extraction_prompt(prompt_dir, version, document_type)` and the default prompt version |
| `src/extraction/extractor.py` | `extract_document(gateway, prompt, text)`, its result and its two errors |
| `src/extraction/store.py` | Reads the documents of a split; reads and saves rows of `document_extractions` |
| `src/extraction/run.py` | `run_extraction(...)`: many documents, one at a time, each result saved as it arrives |
| `src/extraction/accuracy.py` | The exact-match count against the answer keys |
| `pipelines/extract_documents.py` | The command ([02-data.md 2.9](02-data.md#29-running-the-whole-pipeline)) |
| `config/prompts/extraction/<version>/` | One prompt file per document type |

### The schemas

There is one schema per document type. Its field names are the names in that type's answer
key, so scoring is a field-by-field comparison.

| Document type | Schema | Fields |
|---|---|---|
| Denial letter | `DenialLetterExtraction` | 15 |
| Clinical note | `ClinicalNoteExtraction` | 8 |
| Prior-authorisation record | `PriorAuthExtraction` | 12 |

- Every field is `{value, confidence}`. The value may be null, which means "not in the
  text". That is the right answer for a field the noise step blanked.
- A list (the diagnosis codes, the procedure codes, the denied lines of a letter) is one
  field with one confidence for the whole list.
- Dates are parsed as dates, categories as the existing enumerated types, and money as an
  exact decimal. A number in the answer is read straight into a decimal, with no float
  step in between.
- An unknown key, a missing field or a confidence outside 0 to 1 is rejected.
- A schema checks the kind of a value, not whether it is right. A well-formed wrong value
  passes here and is counted as wrong later.

### The prompts

A prompt is a text file, `config/prompts/extraction/<version>/<document type>.txt`. It is
sent as the system text; the document text is the user text. The folder comes from the
setting `PROMPT_DIR` (default `config/prompts`). A version has the form `v1`, `v2`, and so
on. A prompt file holds no secret and no example taken from a stored document.

| Version | What it is |
|---|---|
| `v1` | The first prompts |
| `v2` | The `v1` text plus one rule: when the document gives one service date and not a range, that date is both `service_from_date` and `service_thru_date`. The default |

The `v1` files stay in the project, because stored results name the version that produced
them. A changed prompt gets a new version; an existing file is not edited.

### One document, step by step

1. Build the request: the prompt, the document text, an output limit of 3,000 tokens, the
   prompt version, the purpose `extraction`, and the schema of the document type as the
   response schema (3.8).
2. Call `LlmGateway.complete`. The budget check, the fallback and the spend row are the
   gateway's work.
3. If the answer stopped at the output limit, raise `ExtractionTruncatedError`.
4. Parse the answer into the schema. If it is not JSON or does not fit, raise
   `ExtractionParseError`.
5. Return the fields with the provider that answered, the model name and the prompt
   version.

Nothing is repaired and no second call is made for a failed answer. Both errors are an
`ExtractionError`, and their text never quotes the answer. The gateway's own errors (over
budget, no provider available, a rejected request) pass through unchanged.

Document text and extracted values never appear in a log line or an error message. The one
log line per call is the gateway's.

### The results table

`document_extractions` (migration 0009) has one row per claim, document type and prompt
version: `source_claim_id`, `document_type`, `prompt_version`, `model_name`, `provider`
(`primary` or `fallback`), `text_sha256`, `fields`, `created_at` and `updated_at`.

- It is a public reference table with no `account_id`: it only holds what a model read
  from the fabricated documents of the synthetic claims sample
  (see [conventions 6.4](06-conventions.md#64-database)).
- `fields` is the validated schema as JSON, values and confidences. Dates and amounts are
  stored as text.
- A row is linked to the claim and the document type, not to the row of the document.
  Generating the documents again replaces every document row, and a link to that row would
  delete results that cost money.
- `text_sha256` is the SHA-256 of the text the model read. It tells a result for the
  current text apart from a result for a text that has since been replaced.
- A new prompt version adds rows. It does not overwrite the results of an older version.

### A run over many documents

`run_extraction` takes the documents of one split in a fixed order (claim id, then
document type) and handles them one at a time.

| Situation | What happens |
|---|---|
| A result exists for this prompt version, made from the text the document has now | Skipped; no call is made |
| A result exists, but for an older text | A call is made and the row is replaced |
| The answer is usable | The row is saved and committed at once |
| The answer cannot be parsed, or stopped at the output limit | Counted; nothing is stored; the next run tries the document again |
| The budget is reached, or no provider can answer | The run stops and keeps what it finished |
| A provider rejects the request (a bad request, a wrong key) | The run stops and keeps what it finished; the message gives the error's class name and the status code |

Each result is committed on its own, so a run that stops after an hour is continued by
running the same command again, not paid for twice.

At the end the command prints how many extracted values equal the answer keys exactly, per
document type and field. This count is for comparing two prompt versions. It is **not**
the Stage 3 accuracy target: that measurement belongs to the evaluation harness. A document
with no result is in no count, so the command also prints how many selected documents were
left out. Two runs with different left-out numbers did not compare the same documents.

### Measured

All runs were made on 2026-10-09 on the default load (first 50,000 claims, seed 42), with
the primary provider answering every call.

The prompt was chosen on the first 50 documents of the validation split, never on the test
split:

| Prompt | Extracted | Failed to parse | Values equal to the answer key |
|---|---|---|---|
| `v1` | 50 of 50 | 0 | 540 of 583 (92.6%) |
| `v2` | 49 of 50 | 1 | 544 of 571 (95.3%) |

With `v1`, 9 of the 21 letters had no `service_thru_date`, because a letter prints one date
when the service started and ended on the same day. The rule added in `v2` fixed all 9.

The test split was then run once, with `v2`:

| | |
|---|---|
| Documents selected | 1,883 |
| Extracted | 1,854 |
| Failed to parse | 29 (16 letters, 3 notes, 10 prior-authorisation records) |
| Stopped at the output limit | 0 |
| Answered by the fallback | 0 |
| Time | 6,191 seconds (103 minutes, 3.3 seconds per document) |
| Cost | $0.653694 ($0.000347 per document) |
| Values equal to the answer key | 20,166 of 21,453 (94.0%) |

Per document type: letters 93.8%, notes 94.6%, prior-authorisation records 93.7%. The
weakest fields are the letter's `denied_lines` (550 of 743, 74.0%) and `claim_number`
(647 of 743, 87.1%), the note's `procedure_codes` (657 of 756, 86.9%) and the record's
`request_date` (311 of 355, 87.6%).

The 94.0% leaves out the 29 documents with no result. Counted as wrong on every field,
they bring it to 20,166 of 21,837 (92.3%).

### Known limits

- The confidence is stored as the model reported it. Whether a low confidence goes with a
  wrong value has not been measured, so nothing should rely on it yet.
- The cause of the 29 parse failures is not known, because an answer is never logged or
  stored. They were not tried again.
- The output limit of 3,000 tokens is an estimate. The largest answer in the test run used
  1,094 output tokens.
- A list is right only when every entry is right and in the same order, so one wrong
  denied line makes the whole `denied_lines` field wrong.
- The train split and most of the validation split have not been extracted.
- The fallback answered none of the measured runs, so its extraction quality is not
  measured.
