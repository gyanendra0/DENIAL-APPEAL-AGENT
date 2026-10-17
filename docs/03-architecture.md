# 3. Architecture

## 3.1 One picture

```text
                 ┌──────────────────────────┐
  Browser  ─────▶│  React dashboard (Vite)  │
                 └────────────┬─────────────┘
                              │ HTTPS + JWT cookie
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
| `ml` | Predict appeal success (see 3.10) | Gradient boosting first; a neural net only if it beats it |
| `rules` | Hard policy gates: deadlines, amount floors, must-review cases (see 3.11) | Deterministic, testable, no LLM |
| `rag` | Find and rank supporting evidence (the corpus and its chunks: see 3.13; embedding and search: see 3.14) | pgvector, chunked policy corpus |
| `drafting` | Write an appeal letter from the letter's fields and the evidence found; block a draft with a bad citation (see 3.15) | The model writes labels and quotes; a pure function checks them |
| `agent` | Walk one claim through extraction, rules, scoring, retrieval and drafting, then stop for a person (see 3.16) | Fixed order in code, each step once, a stored trace |
| `llm` | One gateway for all model calls (see 3.8) | Primary API, open-weight fallback, budget guard |
| `api` | HTTP surface, auth, per-account isolation (see 3.18) | FastAPI, JWT in an HttpOnly cookie, role checks |
| `review` | Worklist, denial detail, a person's edit, approve and reject of a draft, the audit log and the job costs (see 3.20) | Every edit and approve checked again; an audit event in the same transaction |
| `jobs` | Turn one uploaded denial letter into a draft in the background: extraction, then the agent loop, then the stored result (see 3.19) | A `jobs` table, run by FastAPI background tasks in the API process |
| `db` | Schema and migrations | SQLAlchemy + Alembic |
| `synth` | Generate Layer C documents | Seeded and reproducible |
| `evaluation` | Measure extraction, model and rules against the stage targets (see 3.12); work out the share of drafts a reviewer accepts (see 3.17) | Read-only, no LLM, nothing stored in the database |

## 3.3 Request flow

1. User uploads a denial. File goes to object storage, a row goes to `denials`.
2. A background job runs extraction; fields are saved with a confidence score.
3. Rules run. A hard fail (for example, the appeal window has closed) stops here.
4. The ML model scores the appeal. Score plus expected value is stored.
5. If the score clears the threshold, the agent retrieves evidence and drafts a letter.
6. The draft appears on the dashboard as **Needs review**.
7. A human edits, approves or rejects. Every action is written to the audit log.

Everything after step 1 is asynchronous. The UI polls job status.

This is the flow of the finished product. What exists today is steps 1 to 5, in two ways.
For the made-up claims of the synthetic sample, a command runs the agent loop on a stored
extraction: rules, score, evidence, draft and a trace that ends in a stop for a person
(see 3.16). For an uploaded denial letter, `POST /uploads` stores the letter's text (not the
file, and not in object storage) and queues a background job that extracts the fields, runs
the same loop and stores the result in the account's own tables; `GET /jobs/{id}` gives its
status (see 3.19). Sign-up, login and the logged-in user's own record work through the API
(see 3.18). Step 7 works through the API: a reviewer reads the worklist and the denial
detail, edits, approves or rejects the draft, and each action is written to the audit log
(see 3.20). The dashboard is not built yet.

## 3.4 Data model (core tables)

| Table | Holds |
|---|---|
| `accounts` | Tenants |
| `users` | Login (email unique across all accounts, Argon2id password hash), role, account link |
| `claims` | Claim header, payer, amounts |
| `denials` | Denial record, reason codes, source file |
| `extractions` | Fields pulled from a document, with confidence and model version |
| `predictions` | Score, model version, features used |
| `evidence_documents` | One published policy document: source, section number, title, version |
| `evidence_chunks` | Policy text cut into chunks |
| `evidence_chunk_embeddings` | The vector an embedding model gave for one chunk, used by the search |
| `appeal_drafts` | A drafted appeal letter that passed the citation validator, for one claim of the synthetic sample |
| `agent_runs` | What the agent loop did with one claim of the synthetic sample: the end state and the trace of the five steps |
| `uploads` | One denial letter a user uploaded: the file name, its type (`text` / `pdf`), its size and the text read from it; the file itself is not kept |
| `jobs` | The background job of one upload: status, failure code, the loop's end state and the times |
| `upload_extractions` | The letter fields a model read from one upload, with the prompt version and the model |
| `upload_agent_runs` | What the agent loop did with one upload: the end state and the trace |
| `upload_drafts` | The checked appeal letter of one upload, when the loop drafted one, and its review: status, who, when, the reject reason, the approved version |
| `draft_revisions` | One version of a draft a person edited; the model's draft stays in `upload_drafts` |
| `audit_events` | One thing a person did: account created, upload, edit, approve, reject; who, when, which job, no text |
| `drafts` | Generated letter, citations, status |

Every business table carries `account_id`, and every query filters on it. This is the
single most important rule in the codebase.

Two kinds of table hold no customer data and have no `account_id`: public reference tables
loaded from public sources, and operational tables such as `llm_calls` (see 3.8). The rule
is in [conventions 6.4](06-conventions.md#64-database).

`accounts` and `users` are the first business tables the product uses: sign-up writes one
row to each, and every API request reads the logged-in user (see 3.18). Since migration 0015
an email belongs to one user of one account, so login can find the account from the email.
That lookup is the one query on `users` without an `account_id` filter.

The five upload tables (`uploads`, `jobs`, `upload_extractions`, `upload_agent_runs`,
`upload_drafts`, migration 0016) are the first business tables that hold a customer's
documents. Each carries `account_id`, and every link between them carries it too, so a row
of one account cannot point at a row of another (see 3.19). They take the place of the
planned `denials`, `extractions` and `drafts` for an uploaded letter: what a model reads from
an upload is in `upload_extractions`, the checked letter in `upload_drafts`. What a model
reads from the generated documents stays in the public reference table
`document_extractions` (see 3.9).

The business table `predictions` does not exist. The win-probability model is trained and
saved as files (see 3.10). A score is stored only in the agent's trace: in `agent_runs` for
a claim of the synthetic sample (see 3.16), in `upload_agent_runs` for an upload (see 3.19).

A rule verdict has no table of its own either. The rules are a function that is called when
a verdict is needed (see 3.11). The agent's trace is the one place a verdict is written
down: the outcome and its reasons, in the public reference table `agent_runs` (see 3.16).

An evaluation result is not stored either. The evaluation harness prints its report and
writes nothing (see 3.12). The verdicts of a draft review are a local file, not a table
(see 3.17). A person's review of an upload's draft is in `upload_drafts` and
`draft_revisions`, and the planned `reviews` and `audit_log` became those columns and the
business table `audit_events` (migration 0017, see 3.20).

`evidence_documents` and `evidence_chunks` exist and are public reference tables: the policy
text is public and shared by every account (see 3.13). The vectors of the chunks are in a
table of their own, `evidence_chunk_embeddings`, also a public reference table (see 3.14).

The business table `drafts` does not exist. A drafted letter for a claim of the synthetic
sample is stored in the public reference table `appeal_drafts` (see 3.15); one for an upload
in the business table `upload_drafts` (see 3.19).

## 3.5 Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Ecosystem for ML and LLMs |
| API | FastAPI + Pydantic | Typed, fast, auto docs |
| ORM / migrations | SQLAlchemy 2 + Alembic | Standard, testable |
| Database | PostgreSQL + pgvector | One store for rows *and* vectors — keeps cost near zero |
| Jobs | A `jobs` table in Postgres, run by FastAPI background tasks in the API process (see 3.19) | No extra broker or worker to pay for or run |
| ML | scikit-learn | Strong on tabular, cheap to train |
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
│  ├─ drafting/     cited appeal letters and the citation validator
│  ├─ evaluation/   measured results against the stage targets
│  ├─ extraction/   document → fields
│  ├─ ingest/       loaders, file handling
│  ├─ jobs/         background jobs: one upload through extraction and the agent loop
│  ├─ llm/          provider gateway, prompts, budget guard
│  ├─ ml/           features, training, inference
│  ├─ rag/          chunking, embedding, retrieval
│  ├─ review/       worklist, denial detail, draft review, audit log, costs
│  ├─ rules/        deterministic policy checks
│  └─ synth/        synthetic document generation
├─ tests/           mirrors src/
├─ pipelines/       runnable end-to-end scripts
├─ frontend/        React dashboard
├─ config/          prompts, evaluation question sets, rule config
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

Every model call in the project goes through one object, the `LlmGateway`. It has two
methods: `complete(request)` for a chat call, which returns text, and `embed(request)` for
an embedding call, which returns vectors. Nothing else talks to a model provider. The
gateway checks the monthly budget before a call, uses a fallback provider when the primary
chat provider cannot be used, and records what each answered call cost. Over budget means
fallback or refuse, never overspend.

Most of this section describes `complete`. What is different for `embed` is under
"Embedding calls" below; how the vectors are used is in 3.14.

`complete` returns text. Parsing that text into a typed schema is the caller's job.

The tests use stub providers only and make no real call. The first real calls were made by
the extraction work (see 3.9).

### Where the code is

| File | Holds |
|---|---|
| `src/llm/gateway.py` | `LlmRequest`, `LlmResult`, the `ChatProvider` interface, `EmbeddingRequest`, `EmbeddingResult`, the `EmbeddingProvider` interface, `LlmGateway` and its errors |
| `src/llm/openai_provider.py` | `OpenAiChatProvider`, the one chat provider class, `OpenAiEmbeddingProvider`, and `build_openai_gateway(settings, session_factory)` |
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

### Embedding calls

`LlmGateway.embed(request)` turns a batch of texts into vectors, one per text. A request
carries the texts, an input version (the name of the recipe that built the texts, see 3.14)
and a purpose. A result carries the vectors in the order of the texts, the model name, the
input version, the input token count and the cost.

1. **Budget check.** The highest possible cost is the UTF-8 byte length of all the texts
   (a token is never smaller than one byte) times the embedding price. If it does not fit
   the month's budget, `LlmBudgetExceededError` is raised and the provider is not called.
2. **Call the embedding provider.** It is the primary provider's service (the same base URL
   and key) with its own model and price.
3. **Record the spend.** One row is written to `llm_calls` for the whole request:
   `provider` is `primary`, `output_tokens` is 0, `prompt_version` holds the request's input
   version, and `model_name` is the embedding model. If the answer has no usage numbers,
   the highest possible token count is recorded.
4. **Log one line** and return the result.

There is no fallback for embeddings. Vectors made by two models cannot be compared, so a
vector from a second model would be of no use to a search. An unavailable provider gives
`LlmUnavailableError`, and a refused request gives `ProviderRejectedError` as for a chat
call.

Two more errors exist only here. `EmbeddingAnswerError`: the provider returned another
number of vectors than texts were sent; the spend is recorded first, because the call was
paid for. `EmbeddingNotConfiguredError`: `embed` was called on a gateway built without an
embedding provider. `build_openai_gateway` always passes one.

The gateway does not check the size of a vector. The code that stores or searches with one
does (3.14).

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
- **A cost meter** adds up what the calls inside one block cost: `with gateway.meter() as
  meter:` counts every call a provider answered, chat or embedding, also one whose spend row
  could not be stored, since it was paid for. A refused call adds nothing. The meter is a
  context variable, so two jobs running at once each see only their own; a meter opened
  inside another adds to the outer one too. A job stores its total in `jobs.cost_usd`
  (see 3.20).

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

An embedding call logs one line of its own, with the number of texts and no output tokens:

```text
llm embedding: model=<model> purpose=evidence_embedding texts=100 input_tokens=17340 cost_usd=0.000347
```

When its spend row could not be stored, the error line is
`llm embedding answered but not recorded: ...` with the same fields.

A log line never holds the prompt, document text, an embedded text, the answer or a key. Printing a request
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
| `LLM_EMBEDDING_MODEL` | yes | Model the embedding calls use, on the primary provider's service |
| `LLM_EMBEDDING_USD_PER_MTOK` | yes | Price of one million embedding input tokens, in US dollars |

The two embedding keys are required for every command that builds a gateway, also one that
makes no embedding call, such as `extract_documents`.

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
stored with the document and is given to the extractor; it is not guessed. An uploaded
denial letter (a text file or a PDF with text) is read by the same extractor with the same
letter prompt, inside its job (see 3.19). OCR is not built: a scanned PDF is refused at
upload.

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
the Stage 3 accuracy target: that measurement belongs to the evaluation harness (see
3.12). A document with no result is in no count, so the command also prints how many
selected documents were left out. Two runs with different left-out numbers did not compare
the same documents.

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

## 3.10 Win-probability model

The model gives a denied claim a win probability: a number from 0 to 1 that says how likely
an appeal is to succeed. It is trained on the stored claims of the synthetic sample.

The label it learns is `appeal_success_proxy`, which the label rule makes up
([02-data.md 2.2.3](02-data.md#223-labels-rule-v2)). So a high AUC means the model learned
that rule. It says nothing about real appeals.

No model call is made and no money is spent: the model is trained and run on this machine.

### Where the code is

| File | Holds |
|---|---|
| `src/ml/features.py` | `build_claim_features(category, lines)`, the feature row `ClaimFeatures` and `FEATURE_VERSION` |
| `src/ml/training.py` | `train_win_model(rows, seed)`, `save_win_model(...)`, the fixed settings and `MODEL_VERSION` |
| `src/ml/inference.py` | `load_win_model(folder)`, `predict_win_probability` and `predict_win_probabilities` |
| `src/ml/auc.py` | `pairwise_auc(true_scores, false_scores)` and the target `TARGET_AUC` |
| `src/ml/win_model_run.py` | `load_training_data(session)` and `train_and_score(data, seed)`: reads the denied claims, fits, scores every split |
| `pipelines/train_win_model.py` | The command ([02-data.md 2.9](02-data.md#29-running-the-whole-pipeline)) |

### The features

A claim becomes one row of three features, built from its stored lines and the denial
reason category of its label:

| Feature | Kind | Meaning |
|---|---|---|
| `denial_reason_category` | Category | The headline denial reason of the claim |
| `total_allowed_charge` | Exact decimal | The allowed charges of all the claim's lines, added up |
| `fully_denied` | True / false | True when every line of the claim is denied |

- These are the three things the label rule looks at, and nothing else. The category alone
  reaches an AUC of only 0.696 on the test split, so the other two are needed.
- The proxy, the hash draw behind it, the split and the claim id are never inputs. A test
  proves that no feature changes when the proxy flips.
- The amount is given to the model as a number, not as the rule's bands. The model finds
  the cut points itself.
- A claim with no lines or no denied line has no feature row: `build_claim_features` raises
  an error.
- A change to the features gets a new `FEATURE_VERSION`.

### The model

The model is scikit-learn's `HistGradientBoostingClassifier`: many small decision trees,
each one correcting the mistakes of the ones before it.

| Setting | Value |
|---|---|
| Trees | 50 |
| Depth of a tree | 3 |
| Learning rate | 0.05 |
| Fewest claims in a leaf | 50 |

- The settings are fixed constants and deliberately small. They are **not** tuned on the
  validation split. 180 settings were tried before the code was written: all scored 0.736
  to 0.756 on the test split, and the validation score could not tell the good ones from
  the bad ones. Picking "the best" would have been picking noise.
- The model is fitted on the train split only. The validation and test splits are scored,
  never learned from.
- The same claims, labels and seed give the same model.
- A change to the settings or the library class gets a new `MODEL_VERSION`.

### The saved files

Training writes two files into the folder named by the setting `MODEL_DIR` (default
`models`, never committed):

| File | Holds |
|---|---|
| `win_model.joblib` | The fitted model |
| `win_model.json` | The facts: feature, model and label rule versions, the scikit-learn version, the split seed, the training seed, per split the rows, the true proxies and the AUC, and the SHA-256 of the model file |

A new run replaces both files. There is no predictions table and no migration: the
score of an upload is stored in its trace (3.19), not in a table of its own.

`load_win_model(folder)` checks before it opens the model file:

1. The facts file exists and is well formed.
2. Its feature, model, label rule and scikit-learn versions are the ones in use now.
3. The SHA-256 of the model file equals the one in the facts file.

Any failure raises `ModelFileError`, and the fix is to train again. The model file is a
pickle, and loading a pickle runs the code inside it. The checks catch a damaged or
out-of-date file. They do not make a file from someone else safe, so the folder must only
hold files this project wrote: never a download or an upload.

### Measured

One run on 2026-10-10 on the default load (first 50,000 claims, split seed 42, label rule
`v2`, training seed 42, scikit-learn 1.9.1). It took 5 seconds.

| Split | Denied claims | Proxy true | Model AUC | Best possible AUC |
|---|---|---|---|---|
| Train | 3,784 | 1,976 (52.22%) | 0.7750 | 0.7722 |
| Validation | 782 | 422 (53.96%) | 0.7641 | 0.7676 |
| Test | 759 | 415 (54.68%) | 0.7539 | 0.7522 |

The best possible AUC is the score of the label rule's own chance: the most any model can
learn, because the rest of the label is a random draw. The test AUC of 0.7539 is above the
Stage 3 target of 0.70. It is 0.0017 above the best possible AUC, which is chance on 759
claims and not a better model.

### Known limits

- The model is no better than a simple one. A logistic regression on the same three
  features (the amount as its logarithm) scored 0.7528 on the test split, measured before the code was written. Gradient
  boosting is used because the build plan names it and it needs no hand-made amount bands,
  not because it scored higher.
- The test AUC is noisy. Over 2,000 resamples of the 759 test claims it ranged from 0.720
  to 0.786.
- The AUC is reported and is not a gate. A run with a test AUC below 0.70 still saves the
  model.
- The features come from the structured claim rows, not from the fields extracted from
  documents (3.9). Only the test split has been extracted, and a denial letter has no
  "fully denied" field.
- A customer's denial cannot be scored yet. The agent loop (3.16) loads the model and
  stores a score in its trace, but only for a claim of the synthetic sample, because the
  features need the claim rows.

## 3.11 Deterministic rules

The rules sort a denied claim into one of three outcomes, using three values printed on its
denial letter:

| Outcome | Meaning |
|---|---|
| `pass` | The claim may go on to scoring and an appeal draft |
| `must_review` | A person must look at the claim before anything else happens |
| `hard_fail` | The claim is stopped: an appeal is closed or not worth making |

The rules are plain code. No model call is made and no money is spent. The same values
always give the same verdict. The rules never send anything and never decide an appeal: they
only say whether the next step may run.

### Where the code is

| File | Holds |
|---|---|
| `src/rules/verdict.py` | The types: `RuleInputs`, `RuleOutcome`, `RuleReason`, `RuleVerdict`, and `RULES_VERSION` |
| `src/rules/deadline.py` | `check_deadline(letter_date, appeal_deadline, today)`, the appeal deadline rule |
| `src/rules/amount.py` | `check_amount(total_allowed_charge_amount, floor)`, the amount-floor rule |
| `src/rules/evaluate.py` | `evaluate_rules(inputs, *, today, amount_floor)`, the one entry point, and `HARD_FAIL_REASONS` |

Callers use `evaluate_rules` and not the single rules, so a claim always gets every check.
There is no command, no table and no migration.

### The inputs

`RuleInputs` holds three values of one denial letter. Each may be `None`, which means the
value is missing:

| Value | Kind | Meaning |
|---|---|---|
| `letter_date` | Date | The date printed on the letter |
| `appeal_deadline` | Date | The last day an appeal may be filed, as printed on the letter |
| `total_allowed_charge_amount` | Exact decimal, 0 or more | The allowed charges of all the claim's lines, added up |

Two more values are passed to `evaluate_rules` on every call, with no default:

- `today`: the date the deadline is compared with.
- `amount_floor`: the smallest amount the rules let through (see Settings below).

Nothing in `src/rules` reads the clock, the settings or an extraction result. The caller
takes the plain values out of the extraction (3.9) and passes `today` and the floor in. A
test reads the source files of `src/rules` to keep it that way. This is why the same call
always gives the same verdict, and why a test can pick any date.

The amount must be a `Decimal`. A float, a whole number or text is refused, so a caller
cannot pass a rounded amount by mistake.

### The rules

Six reasons can fire. Each belongs to one rule and leads to one outcome:

| Reason | Fires when | Outcome |
|---|---|---|
| `deadline_not_after_letter_date` | The deadline is on or before the letter date | `must_review` |
| `deadline_missing` | There is no deadline | `must_review` |
| `deadline_passed` | `today` is after the deadline | `hard_fail` |
| `amount_missing` | There is no amount | `must_review` |
| `amount_zero` | The amount is exactly 0 | `must_review` |
| `amount_below_floor` | The amount is above 0 and below the floor | `hard_fail` |

The deadline rule:

- On the deadline day itself the appeal is still open. Only the day after is too late.
- A deadline on or before the letter date looks wrong, so the claim goes to a person and is
  **not** reported as passed. A value that looks wrong must never close a claim.
- A missing deadline goes to a person. The rule never guesses one.
- A missing letter date leaves the date-order check out. The passed check still runs.
- The rule reads the deadline as printed and never computes one. The 180-day appeal window
  on the generated letters is made up
  ([02-data.md 2.4.1](02-data.md#241-generated-so-far-denial-letters-v1)), so no real law is
  used.

The amount rule:

- An amount exactly at the floor is big enough.
- Zero is never "below the floor". Zero means the letter does not say what the claim was
  worth, so the claim goes to a person.
- A negative amount raises `ValueError`. So does a floor that is not an exact decimal of 0 or
  more: a negative one, a float, "not a number" or infinity.

A deadline reason and an amount reason can fire together. Two reasons of the same rule
cannot.

### The verdict

`evaluate_rules` always runs both rules and returns one `RuleVerdict`:

| Field | Holds |
|---|---|
| `outcome` | `pass`, `must_review` or `hard_fail` |
| `reasons` | Every reason that fired, deadline reasons first; empty for `pass` |
| `rules_version` | `RULES_VERSION`, today `v1` |

The outcome follows from the reasons, in this order:

1. Any hard-fail reason (`deadline_passed`, `amount_below_floor`) gives `hard_fail`,
   whatever else fired.
2. Otherwise any reason gives `must_review`.
3. No reason gives `pass`.

Every reason is listed, not only the first, so a reviewer sees all that is wrong with a
claim. A change to a rule, a reason or the order of the reasons gets a new `RULES_VERSION`.

`RuleVerdict` is only a container: it does not check that its outcome fits its reasons.
That is one more reason to build a verdict with `evaluate_rules` only.

### Settings

| Key | Required | Meaning |
|---|---|---|
| `RULES_AMOUNT_FLOOR_USD` | no (default 25) | Smallest total allowed charge the rules let through, in US dollars; an exact decimal of 0 or more |

The value 25 is **assumed**. No source gives it: it was chosen from the made-up data, where
it stops 395 of 5,325 claims (7.42%); 50 would stop 863 (16.21%). A floor of 0 turns the
below-floor check off. A negative value, text or an empty value fails when the settings are
loaded. The caller passes `settings.rules_amount_floor_usd` to `evaluate_rules`.

### Measured

Measured on 2026-10-10 with `evaluate_rules` on the default load (first 50,000 claims,
split seed 42, 5,325 denial letters), read-only, with a floor of 25. The date passed as
`today` is 2010-01-01, chosen because it falls in the middle of the made-up deadlines.

On the answer keys of all 5,325 letters:

| Outcome | Claims |
|---|---|
| `pass` | 2,264 (42.52%) |
| `must_review` | 278 (5.22%) |
| `hard_fail` | 2,783 (52.26%) |

| Reason | Claims |
|---|---|
| `deadline_passed` | 2,606 |
| `amount_zero` | 566 |
| `amount_below_floor` | 395 |

A claim can have two reasons, so the reasons add up to more than the outcomes. The other
three reasons do not fire, because an answer key has every value and a correct deadline.

On the values a model extracted from the test split's letters (3.9), 743 letters have a
stored result. The verdict from the extracted values is the same as the verdict from the
answer key on 735 of them (98.92%), with a deadline the noise step blanked counted as truly
missing. The other 8:

| Answer key | Extracted values | Letters |
|---|---|---|
| `pass` | `must_review` | 5 |
| `hard_fail` | `must_review` | 2 |
| `pass` | `hard_fail` | 1 |

So a wrong extracted value sends a claim to a person 7 times and wrongly stops it once. No
claim that should be stopped passes.

These counts are from the day the rules were written. More letters have a stored result
since then; the current agreement count is printed by the evaluation harness and is in 3.12.

### Known limits

- With the real date every made-up claim is past its deadline: the deadlines run from
  2008-07-08 to 2011-08-04, so `deadline_passed` fires on all 5,325. Any run over the
  made-up data must pass a chosen date as `today`.
- One extracted test letter is wrongly stopped: its total allowed charge is 110.00 and was
  extracted as 11.00, which is below the floor. Its dates were right. No rule can catch
  that from the letter alone.
- Three wrong extracted deadlines (8, 10 and 92 days too early) pass every check. Only the
  letter date plus a known appeal window would catch them, and the window is made up, so it
  is not a rule input.
- The model's confidence scores are not used. They sit on a few round numbers (0.9, 0.95,
  1.0) and do not separate a right value from a wrong one: flagging every letter with a
  deadline or letter-date score below 0.95 would send over half the letters to review.
- A failed extraction is not a rule. There are no values to pass in, so the caller must
  send such a claim to a person. 16 test letters have no stored result.
- The floor is on the total allowed charge, the only printed number that says how big a
  claim is. It is not the money an appeal could win: the generated letters print no billed
  amount.
- Two places call `evaluate_rules` outside the tests: the evaluation harness (3.12) and the
  agent loop (3.16). Each passes its own `today`, so both must be given the same date for
  their verdicts to be compared.

## 3.12 Evaluation harness

The evaluation harness is one command that measures what Stage 3 built and checks it
against the Stage 3 done condition in [05-build-plan.md](05-build-plan.md): on the test
split, field extraction accuracy of at least 85% and a model AUC of at least 0.70.

The command only reads. It makes no model call, spends no money, trains nothing and writes
nothing. Running it twice on the same database gives the same report.

All data it measures is generated: made-up documents of the synthetic claims sample, and an
appeal-success label that is a proxy made by the label rule
([02-data.md 2.2.3](02-data.md#223-labels-rule-v2)). The numbers say how well the parts
work on that data. They say nothing about real appeals, and the report's first line says
so.

### Where the code is

| File | Holds |
|---|---|
| `src/evaluation/extraction_accuracy.py` | `build_extraction_accuracy_report(documents, rows)`, the extraction counts, and `TARGET_FIELD_ACCURACY` |
| `src/evaluation/model_score.py` | `score_saved_model(model, data, split)`, the saved model's AUC on one split |
| `src/evaluation/rules_agreement.py` | `build_rules_agreement_report(documents, rows, *, today, amount_floor)`, how often the rules give an extracted letter the verdict its answer key gets |
| `src/evaluation/report.py` | `build_evaluation_report(...)`, the combined `EvaluationReport`, its printed text and `targets_met` |
| `pipelines/run_evaluation.py` | The command: reads the database and the saved model, prints the report, sets the exit code |

The four functions in `src/evaluation` are pure: the documents, the stored results, the
model and the dates are passed in. Only the command opens the database, reads the settings
and loads the model file. `src/evaluation` calls the public functions of `src/extraction`,
`src/ml` and `src/rules` and holds no rule, schema or model of its own. There is no table
and no migration. The command and its options are in
[02-data.md 2.9](02-data.md#29-running-the-whole-pipeline).

### What each number means

**Field accuracy.** Every field of every document's answer key is one value. A value is
right when the extracted value equals the answer key exactly (the same comparison as the
count in 3.9). Field accuracy is the right values divided by all values.

Three rules decide what is counted:

- **The truth is the value before noise.** The answer key holds what the generator wrote,
  not what the noise step left on the page
  ([02-data.md 2.4.4](02-data.md#244-noise-v1)). A model that copies a damaged character
  faithfully is counted as wrong, because that is what a bad scan does to a real system.
  For the one field the noise step blanked on a document, the truth is "no value".
- **A document with no result counts as all wrong.** When the model's answer could not be
  used, nothing is stored (3.9), and every value of that document is counted as wrong. A
  document the system could not read is a failure of the system; leaving it out would let
  a run look better by failing more often.
- **A stale result is no result.** A stored result made from a text that is no longer the
  document's text (its `text_sha256` differs) is treated as missing.

The report prints the accuracy both ways: with such documents counted as wrong, and with
them left out. Only the first is checked against the target.

**Headline denial reason.** The share of denial letters whose extracted
`denial_reason_category` equals the answer key, with a letter that has no result counted as
wrong. It is one field of the field accuracy, shown on its own because
[01-problem-and-scope.md 1.6](01-problem-and-scope.md#16-success-criteria) names "correct
denial-reason extraction" as a success measure, and because it is one of the model's three
features.

**Model AUC.** The AUC of the saved win-probability model (3.10) on the claims of the
split, built from the stored claim rows and labels. The model is loaded from the folder
named by `MODEL_DIR`; it is not trained here. The best possible AUC, the score of the label
rule's own chance, is printed beside it.

**Rules agreement.** For each denial letter the rules (3.11) are run twice: on the answer
key's values and on the extracted values. The report counts how often the two verdicts
have the same outcome, and lists each difference by direction. A value the noise step
blanked counts as truly missing on the answer-key side. A letter with no result, a stale
result or a negative extracted amount gets no verdict from extraction and is counted apart.

**Noise levels and confidence bands.** Field accuracy again, split by the document's noise
level and by the confidence score the model gave each value, over documents with a result.

### Gates and reported numbers

Three numbers are gates. The command exits with 0 only when all three were measured and
meet their targets:

| Number | Target |
|---|---|
| Field accuracy, no result counted as wrong | at least 85% (`TARGET_FIELD_ACCURACY`) |
| Headline denial reason, no result counted as wrong | at least 85% (the same constant) |
| Model AUC on the split | at least 0.70 (`TARGET_AUC` in `src/ml/auc.py`) |

A number that cannot be measured is not met. That is the case when there is no saved
model, when the stored labels cannot be used (the same checks as the training command), or
when the split has only one kind of proxy. The report is still printed, with the reason on
the model line.

Everything else is reported and never changes the exit code: the accuracy with documents
left out, per document type, per field, per noise level, per confidence band, and the rules
agreement. The build plan has no target for them.

A printed number is rounded normally, with one exception: a number just below its target
is rounded down, so the report can never show `85.00%` beside `below the target`.

### Measured

Measured on 2026-10-10 on the default load (first 50,000 claims, split seed 42), test
split, prompt `v2`, rules date 2010-01-01, amount floor 25. The command took about 8
seconds and exited with 0.

Against the Stage 3 done condition:

| Number | Measured | Target | Result |
|---|---|---|---|
| Field accuracy | 20,317 of 21,837 (93.04%) | 85% | met |
| Headline denial reason | 690 of 759 (90.91%) | 85% | met |
| Model AUC, test split | 0.7539 on 759 denied claims | 0.70 | met |

The test split has 1,883 documents: 1,867 with a current result, 16 with none, 0 stale.
With the 16 left out, field accuracy is 20,317 of 21,619 (93.98%). The best possible AUC on
the test split is 0.7522, so the model has learned the label rule and no more.

Per document type, no result counted as wrong:

| Type | Documents | With a result | Field accuracy |
|---|---|---|---|
| Denial letter | 759 | 749 | 10,533 of 11,385 (92.52%) |
| Clinical note | 759 | 758 | 5,739 of 6,072 (94.52%) |
| Prior-authorisation record | 365 | 360 | 4,045 of 4,380 (92.35%) |

Per noise level, documents with a result:

| Noise level | Field accuracy |
|---|---|
| `none` | 3,852 of 3,900 (98.77%) |
| `light` | 10,601 of 11,096 (95.54%) |
| `heavy` | 5,864 of 6,623 (88.54%) |

Noise is what costs accuracy: on a clean document the model is right on almost every value.

The weakest fields, over documents with a result (every other field is at 90% or more):

| Field | Right |
|---|---|
| Letter `denied_lines` | 556 of 749 (74.23%) |
| Note `procedure_codes` | 659 of 758 (86.94%) |
| Letter `claim_number` | 652 of 749 (87.05%) |
| Prior-auth `request_date` | 316 of 360 (87.78%) |
| Prior-auth `provider_name` | 320 of 360 (88.89%) |
| Prior-auth `authorization_number` | 323 of 360 (89.72%) |

Per confidence score the model gave, documents with a result:

| Score | Values right |
|---|---|
| below 0.80 | 166 of 187 (88.77%) |
| 0.80 to 0.89 | 228 of 337 (67.66%) |
| 0.90 to 0.94 | 7,643 of 8,332 (91.73%) |
| 0.95 to 0.99 | 2,045 of 2,126 (96.19%) |
| 1.00 | 10,235 of 10,637 (96.22%) |

The score does not rise steadily with correctness, which is why no rule uses it (3.11).

Rules agreement: of the 759 test letters, 749 get a verdict from extraction and 10 do not.
The outcome is the same as the answer key's on 739 of the 749 (98.66%). The other 10:

| Answer key | Extracted values | Letters |
|---|---|---|
| `pass` | `must_review` | 7 |
| `hard_fail` | `must_review` | 2 |
| `pass` | `hard_fail` | 1 |

Nine of the ten differences send a claim to a person. One stops a claim wrongly (see Known
limits). No claim that should be stopped passes.

### Known limits

- The numbers hold only while the table `document_extractions` is unchanged. Another
  `extract_documents --split test` run tries the 16 documents with no result again and
  could move every extraction number.
- 16 test documents have no result after two tries (10 letters, 1 note, 5 prior-auth
  records). Why their answers could not be used is not known, because an answer is never
  logged.
- The letter's `denied_lines` is right on 74.23% of letters, the only field below 85%. The
  gate is on all fields together, so this field does not fail it.
- There is no AUC from extracted letters. The model's third feature, whether every line was
  denied, is not printed on a letter. With the guess "the total payment is 0" (wrong on 54
  of the 759 answer keys), extracted values give an AUC of 0.7267 on 749 letters, against
  0.7521 from the stored rows of the same claims. That number was measured once by hand; the
  command does not print it, because a guessed feature is a change to the model's inputs
  and not a measurement.
- One test letter is wrongly stopped by the rules: its total allowed charge of 110.00 was
  extracted as 11.00, below the floor of 25. No check on the letter alone catches a dropped
  digit.
- The AUC is measured on the stored claim rows, the same inputs the model was trained on,
  not on values read from a document.
- The exit code depends on the rules date only through the reported lines: `--rules-today`
  changes the rules agreement and never a gate. Any ISO date form Python reads is accepted
  (`20100101`, `2010-W01-1`), not only `YYYY-MM-DD`.
- When `MODEL_DIR` names a file instead of a folder, the command ends with a traceback
  instead of the "cannot be measured" line.

## 3.13 Evidence corpus

The evidence corpus is the policy text a draft may cite. Stage 4 builds it in two steps.
This section covers the first: reading one public source, cutting its text into chunks and
storing them. The second step, embedding the chunks and searching them, is in 3.14.

Two sources are loaded:

| Source | What it is | Published as | Details |
|---|---|---|---|
| `cms_ncd` | The CMS national coverage determinations: one decision per service | HTML inside a csv file | [02-data.md 2.3.1](02-data.md#231-loaded-source-cms-national-coverage-determinations) |
| `cms_manual` | Four chapters of the CMS Internet-Only Manuals: the general rules on coverage, exclusions, appeals and liability | PDF | [02-data.md 2.3.2](02-data.md#232-loaded-source-cms-manual-chapters) |

What each file is, what is loaded from it, the quality gates, the command and the measured
numbers are behind the two links.

Nothing here calls a model or spends money. The HTML is read with Python's own
`html.parser`. The PDF files are read with the library `pypdf`.

### Where the code is

| File | Holds |
|---|---|
| `src/db/models.py` | `EvidenceDocument`, `EvidenceChunk` and the enum `EvidenceSource` |
| `src/db/migrations/versions/rev_0010_evidence_corpus.py` | Migration 0010: the type `evidence_source` and the two tables; the downgrade drops all three |
| `src/db/migrations/versions/rev_0014_evidence_source_cms_manual.py` | Migration 0014: the value `cms_manual` in the type `evidence_source` (see "The load" for its downgrade) |
| `src/rag/chunking.py` | `chunk_document(sections)`, the chunk rules, `CHUNKER_VERSION` and `MAX_CHUNK_CHARS` |
| `src/ingest/html_paragraphs.py` | `html_to_paragraphs(html)`: one HTML field to plain paragraphs |
| `src/ingest/evidence_corpus.py` | `read_evidence_corpus(path)` with the quality gates, `replace_evidence_corpus(session, batch)`, and `replace_source_documents(session, source, entries)`, the store step both sources share |
| `pipelines/load_evidence_corpus.py` | The command for the determinations: reads the file, stores it in one transaction, prints the counts, sets the exit code |
| `src/ingest/pdf_text.py` | `read_pdf_pages(path)`: the text of a PDF, page by page. The only module that imports `pypdf` |
| `src/ingest/manual_chapters.py` | `read_manual_chapters(paths, *, source_file_date)` with the quality gates, and `replace_manual_chapters(session, batch)` |
| `pipelines/load_manual_chapters.py` | The command for the manual chapters: the same steps, for one or more PDF files and a `--file-date` |

The work is split so that each part knows one thing. A reader knows its file: its layout
and its gates. The chunker knows nothing about a file: it takes plain paragraphs and
returns chunks, and it reads no file, no setting and no database. The manual chapters were
added as a second reader; the chunker, its version and its limit did not change.

### The two tables

Both are public reference tables: loaded from a public source, shared by every account, no
`account_id` ([conventions 6.4](06-conventions.md#64-database)).

`evidence_documents` has one row per source document:

| Column | Holds |
|---|---|
| `source` | Where the document comes from, a constrained type with two values: `cms_ncd` and `cms_manual` |
| `source_document_id` | The document's id within its source; unique within a source. A determination: the database's own `NCD_id`. A manual section: publication, chapter and section number, such as `100-04-29-310.2` |
| `section_number` | The number a reader would cite; unique within a source. A determination: such as `220.6.17`. A manual section: the same value as its id |
| `title` | The document's title |
| `version_number` | The version that was loaded, at least 1 |
| `effective_date` | The date that version took effect |
| `source_file_date` | The date of the downloaded file the row was read from |

`evidence_chunks` has one row per chunk:

| Column | Holds |
|---|---|
| `evidence_document_id` | The document the chunk belongs to; removing a document removes its chunks |
| `chunk_index` | The chunk's place in its document, from 0, in reading order; unique within a document |
| `section_title` | The part of the document the chunk comes from; may be NULL for a source without parts (an empty text is refused) |
| `text` | The chunk's text |
| `text_sha256` | The SHA-256 hash of `text` |
| `chunker_version` | The version of the rules that cut the text |

There are two tables because a citation names one chunk of one document, and a document's
title, section number and version are stored once, not on every chunk.

### From HTML to paragraphs

The policy text is published as pieces of HTML. `html_to_paragraphs` keeps the words and
the paragraph breaks and drops everything else:

- A paragraph ends at each of these tags: `p`, `div`, `li`, `ul`, `ol`, `table`, `tr`,
  `hr`, `br`. A line end inside a paragraph is only a wrapped line, not a break.
- A table gives one paragraph per row, with its filled cells joined by ` | `.
- Entities such as `&sect;` are decoded. Every run of spaces, tabs, line ends and
  non-breaking spaces becomes one space. A paragraph with no text is dropped.
- A paragraph whose whole text is bold is marked as a heading.
- These tags are dropped and their words stay in the paragraph: `a`, `em`, `u`, `sup`,
  `sub`, `font`, `tbody`.
- Any other tag (a heading tag, `script`, `style` and so on) is refused, and the loader
  rejects the file. A guess would join two words or keep text that is not policy.

Each of the two policy columns becomes one section with a fixed title. The titles are
shortened from the file's data dictionary; they are not text of the file. A column with no
text gives no section.

### From a PDF chapter to paragraphs

A manual chapter is one PDF file. `read_pdf_pages` returns the text of each page;
`read_manual_chapters` joins the pages and finds the sections in six steps:

1. The first two lines name the manual and the chapter. The manual's name gives the
   publication number (`100-02` or `100-04`). An unknown manual is refused.
2. The table of contents is read as the list of numbered sections to find, and then left
   out of the text.
3. Each section's heading is looked for in the body, in the order of that list. A line that
   starts with a number is not a heading on its own: the text holds many numbered lines
   that are not headings, so only a number of the list counts.
4. The line under a heading is the section's revision line, such as
   `(Rev. 12, Issued: 01-02-24, Effective: 03-04-24, Implementation: 03-05-24)`. The
   revision number becomes the version number. The effective date is taken from the line;
   when the line gives none, or a value that is not a date, the issued date is used. An
   older heading can end with its revision line (`20.1 - Title (Rev. 1, 10-01-03)`); that
   one is read only when no line under the heading is a revision line. A section with no
   revision line at all gets version 1 and the file date.
5. The table "Transmittals Issued for this Chapter" at the end is left out.
6. Inside a section, a line that holds only whitespace ends a paragraph. A line that ends
   in a hyphen is joined to the next one without a space when that one starts with a small
   letter, so a word cut at a line end is whole again.

Each section becomes one document with one unnamed part, so the `section_title` of a manual
chunk is NULL. A section whose heading is followed directly by its subsections has no text
of its own; it is counted and left out. The title is the heading's text, cut to 300
characters.

The four files have no page header, page footer or page number in their text, so no
repeated text has to be removed. The two things that are cut are the table of contents and
the transmittals table. The reader does not try to remove repeated page text. It rejects a
file that has any, before anything else is read: the same line (with every number read as
the same, so `15-3` and `15-4` are one line) at the top or at the bottom of more than half
of the pages that hold text, or a page that starts or ends with a line like `Page 3 of 12`.
In the four files the most repeated line is at the top of 18 of the 311 pages of chapter
15, far below half.

`read_pdf_pages` turns every way a file can fail to open into a rejected file. The PDF
library raises its own errors for most damaged files and plain Python errors for some (a
damaged cross-reference table, for example), so both kinds are caught. A file that cannot
be opened (no read permission, for example) is rejected too. Only a missing file is left as
`FileNotFoundError`; the command checks that every path is a file before it reads, and
exits 2.

### How a text is cut into chunks

`chunk_document(sections)` applies the rules of chunker `v1`:

1. Paragraphs are packed in reading order, joined by a blank line, until the next one
   would take the chunk over 1,200 characters (`MAX_CHUNK_CHARS`).
2. A chunk never holds text of two sections. `chunk_index` counts from 0 across the
   sections of one document.
3. A chunk does not end on a heading when the heading fits at the start of the next
   chunk, so a heading stays with the text it introduces.
4. A paragraph over the limit is cut at sentence ends (after `.`, `;`, `:`, `?` or `!`).
   A sentence over the limit is cut at a space, and a word over the limit at the limit.
5. Chunks do not overlap: every word is in exactly one chunk.

The limit of 1,200 was set from the real text: the inspection of the file counted 4,539
paragraphs in the two policy columns, 12 of them longer than 1,200 characters, and each of
the 12 could be cut at a sentence end. The limit is a constant, not an option. The manual
chapters use the same limit; the inspection of the four files counted 65 of 5,512
paragraphs over it.

The same text always gives the same chunks. Any change to a rule or to a constant needs a
new `CHUNKER_VERSION`, and every stored chunk carries the version that made it.

### What a citation will point at

A citation names one chunk by four values: the document's `source` and
`source_document_id`, the chunk's `chunk_index` and the chunk's `text_sha256`. It never
uses a row id: the load replaces the rows, so every document and chunk gets a new id each
time. For the same reason no later table may point a foreign key at `evidence_chunks.id`
and expect it to survive a load.

For a manual section the four values look like this: `cms_manual`, `100-04-29-310.2`, the
chunk's index and its hash. The id holds the publication, the chapter and the section
number, so a reader can find the passage in the published manual from the citation alone.
The source is part of the citation because an id is unique only within its source.

The hash is what makes a citation checkable later. The load replaces the rows, so a new
file can change the text behind a document and index. The stored hash then differs from the
one in an older citation, and the citation is known to be out of date instead of silently
pointing at other words. The drafting step can also compare a quoted sentence with the
exact stored text.

Citations are written and checked by the drafting step (3.15).

### The load

`replace_source_documents(session, source, entries)` removes every stored document of one
source, which removes its chunks too, and then stores the entries. It does not commit: the
command runs it in one transaction, so a failure brings the old rows back. An update in
place would not do, because a document that left the file would keep its old chunks.

Both sources use it: `replace_evidence_corpus` calls it with `cms_ncd`,
`replace_manual_chapters` with `cms_manual`. Only rows of the named source are removed, so
a load of one source leaves the documents, chunks and vectors of the other as they are. An
entry of another source than the named one raises `ValueError` before anything is removed.

A manual load makes the stored manual sections exactly the sections of the files named in
that one run. A chapter that is not named is removed, so all four chapters are named each
time.

**Migration 0014 and its downgrade.** The upgrade adds the value `cms_manual` to the type
`evidence_source` and writes no row. Postgres cannot take a value out of such a type, so
the downgrade deletes every `cms_manual` document (its chunks and vectors go with it),
builds the type again with `cms_ncd` only and switches the column to it. A citation of a
manual section that is already stored inside a draft or a trace is plain JSON and stays.

### Where the vectors are

`evidence_chunks` has no vector column. The vectors are stored in a table of their own,
`evidence_chunk_embeddings`, linked to the chunk (3.14). Removing a chunk removes its
vectors, so **a load leaves its source without vectors**: run
`python3 -m pipelines.embed_evidence_corpus` after every load of either source. Until then
the search cannot return a chunk of the source that was loaded.

### Measured

Measured on 2026-10-10 on the file downloaded that day (file date 2026-10-05). The command
took about a second and exited with 0.

| Number | Value |
|---|---|
| Rows in the file | 357 |
| Determinations loaded | 316 |
| Retired notices left out | 41 |
| Chunks | 1,120 |
| Chunks per determination | median 2, most 18 |
| Chunk length in characters | longest 1,200, median 855.5, shortest 13 |
| Chunks under 100 characters | 22 |
| Characters stored | 866,134 |

No stored chunk holds a leftover tag, entity, tab or non-breaking space. A second run on
the same file gave the same 1,120 hashes.

**The manual chapters**, loaded on 2026-10-12 on the local database, after migration 0014,
from the four files downloaded on 2026-10-11 (the file date). The command took about 19
seconds and exited with 0.

| Chapter | Sections in the contents | Sections loaded | Chunks |
|---|---|---|---|
| 100-02 chapter 15 | 223 | 220 | 849 |
| 100-02 chapter 16 | 38 | 38 | 98 |
| 100-04 chapter 29 | 105 | 101 | 293 |
| 100-04 chapter 30 | 294 | 277 | 513 |
| All four | 660 | 636 | 1,753 |

| Number | Value |
|---|---|
| Sections with no text left out | 24 |
| Sections without a revision line (version 1, the file date) | 0 loaded; the one such section has no text |
| Sections with the issued date as effective date | 137 |
| Chunks per section | median 1, most 47 (100-02 chapter 15, section 220.3) |
| Chunk length in characters | longest 1,200, median 939, shortest 7 |
| Chunks under 100 characters | 22 |
| Characters stored | 1,476,499 |
| Longest id | 19 characters, of 20 allowed |
| Version numbers | 1 to 13889 |
| Effective dates | 2003-01-01 to 2026-08-28 |

The determinations were checked before and after the load: 316 documents, 1,120 chunks and
1,120 vectors both times, with the same chunk hashes and the same vector rows.

### Known limits

About what the corpus can support:

- The claims are from 2008 to 2010 and the policy text is the version current in 2026. A
  cited determination may not have applied on the date of service.
- The generated letters name made-up payers, while the evidence is Medicare policy. Drafts
  on this data show the mechanism (retrieve, cite, check); they are not real appeals.
- The determinations may cover few of the denied services, and the file cannot tell: no
  file in the download says which procedure codes a determination is about
  ([02-data.md 2.3.1](02-data.md#231-loaded-source-cms-national-coverage-determinations)).
- No description of a CPT code is stored. Retrieval can match a denied service only when
  the policy text itself names it.
- The manual text is the version current in 2026 too, with sections revised between 2003
  and 2026. The same gap to the 2008 to 2010 claims holds.
- Almost nothing loaded is about `coordination_of_benefits`. That is the Medicare Secondary
  Payer manual (publication 100-05), which is not loaded.
- When the chapters were loaded it was not known whether a manual passage is found for a
  claim of the sample. It is since search question `v3` (3.15): in the loop run of
  2026-10-12 every one of the 54 drafts cites a manual passage, with a similarity of at
  least 0.6295 (3.17). One of the cited sections, `100-02-16-40.3`, is about a patient with
  other health coverage, so the chapters do hold a little on `coordination_of_benefits`.
  The passages are general rules for a denial reason, the same for every claim with that
  reason; none is about the denied service.

About the text:

- List numbers that the source keeps in attributes (such as `<ol type="a">`) are lost. A
  sentence that refers to "item (b)" may no longer show which item that is.
- A link keeps its words and loses its address.
- A tag that is cut off before its closing `>` (such as `<p class=` at the very end of a
  field) is kept as text. No gate catches it.
- A `<` written in a sentence and followed directly by a letter is read as a tag. When the
  letters are a known tag name (`(<p 0.05)`), the text up to the next `>` is lost and no
  gate catches it. With any other letters (`<age 65`), the file is rejected as holding an
  unknown tag. A `<` followed by a digit or a space stays as text.
- Comments and document declarations in the HTML are dropped with their content, and the
  words on both sides of one are joined.
- An empty table cell leaves no separator, so in a row with an empty cell the later cells
  move one place to the left.
- A chunk from the middle of a long table has no header row. The Durable Medical Equipment
  Reference List (section 280.1) is such a table.
- A determination is left out as retired only when its title holds `RETIRED`. Section 40.5
  is loaded although its whole text is a note that it was removed from the manual.
- One sentence in the policy text of one determination (`NCD_id` 167) is worded like a
  procedure-code description, without a code number. Who owns that wording is not verified.
  It is loaded as published.
- 22 chunks are shorter than 100 characters, the shortest 13. A chunk that short carries
  little to retrieve on.

About the text of the manual chapters (what the PDF reader loses):

- A table loses its columns: the cells of a row come out as running text, and sometimes in
  another order. The largest is the benefit category table in 100-02 chapter 15, section
  110.8 (29 chunks), which lists HCPCS Level II codes with their descriptions.
- Exhibits and model letters that sit inside a section (chapters 29 and 30) are loaded as
  ordinary text of that section.
- Older reference lines such as `B3-2005` under a heading are kept as text. For one section
  (100-02 chapter 16, section 50.1) such a line is the whole text.
- A paragraph break is taken from a line of only whitespace. The inspection estimated that
  this is wrong for about 1 to 8% of the paragraphs: two paragraphs joined, or one split.
- The 14 images and the marks that show which words a revision changed are lost. What the
  images show was not looked at.
- The text was not compared with the visible page.
- Lettered sub-headings inside a section (`A. General`) are not marked as headings, so a
  chunk can end on one.
- Chapter 15 mentions CPT code numbers in its text. They are loaded as published; no
  passage was checked one by one for third-party descriptions.
- 137 sections have a revision line with no usable effective date (such as
  `(Rev. 1, 10-01-03)`); their effective date is the issued date. A section with no
  revision line would get version 1 and the file date; none of the loaded sections is one.
- The repeated-page-text gate looks only at the first and the last line of each page. A
  page header or footer on half of the pages or fewer, one of two lines, or one whose words
  change from page to page, is not seen and would be loaded as text. A file of very few
  pages can be rejected wrongly: three pages that end in `100`, `250` and a word have "the
  same line" at the bottom of 2 of 3 pages, because every number is read as the same. The
  shortest of the four chapters has 39 pages.
- A revision line inside a heading that cannot be read (`(Rev. 5, 13-45-03)`, a date that
  does not exist) does not reject the file, as the same line under the heading would. The
  section gets version 1 and the file date and is counted in the report line "sections
  without a revision line".
- 22 chunks are shorter than 100 characters, the shortest 7.

About the load:

- The date of the manual files is typed by the person who runs the command. Only a date in
  the future is refused; a wrong past date is stored as given.
- A manual load removes every chapter that is not named in it.
- A load is not locked out while an embedding run is in progress (3.14, "The embedding
  run"). This holds for both load commands.
- A load of either source needs a new embedding run, and that run pays for every chunk of
  the loaded source again.
- There is no gate on the number of rows. A cut-off download that still parses is loaded.
- The file states no release date. The stored date is the one the zip records for the csv
  file. It is not a date CMS states for the data.

## 3.14 Evidence retrieval

Retrieval makes the stored policy text searchable by meaning. It is the second step of the
evidence work: 3.13 stores the chunks, this section gives each chunk an embedding and
searches them.

An embedding is a list of numbers (a vector) that an embedding model gives for a text. Two
texts about the same thing get vectors that point in nearly the same direction. A search
turns a question into a vector in the same way and returns the chunks whose vectors are
closest to it.

Every embedding call goes through the gateway and passes the budget check first (3.8). The
corpus is public policy text and holds no patient data. Nothing here drafts a letter or
builds a question from a claim; that is the drafting step (3.15).

### Where the code is

| File | Holds |
|---|---|
| `src/llm/gateway.py`, `src/llm/openai_provider.py` | `LlmGateway.embed(request)` and `OpenAiEmbeddingProvider` (3.8, "Embedding calls") |
| `src/db/models.py` | `EvidenceChunkEmbedding` and `EMBEDDING_DIMENSIONS` |
| `src/db/migrations/versions/rev_0011_evidence_chunk_embeddings.py` | Migration 0011: the table; the downgrade drops it and keeps the `vector` extension |
| `src/rag/embedding_input.py` | `build_embedding_input(document_title, section_title, text)` and `EMBEDDING_INPUT_VERSION` |
| `src/rag/embedding_run.py` | `run_evidence_embedding(session_factory, gateway, *, model_name)`: embeds the chunks that have no vector |
| `src/rag/retrieval.py` | `search_evidence_by_vector`, `search_evidence`, the result model `EvidenceMatch` and the two errors |
| `src/evaluation/search_quality.py` | `read_search_questions`, `unknown_section_numbers`, `run_search_quality_check`: the search quality check |
| `pipelines/embed_evidence_corpus.py` | The command that embeds the stored chunks |
| `pipelines/check_evidence_search.py` | The command that runs the search quality check |
| `config/evaluation/evidence_search_questions.json` | The project's 40 hand-made questions, 20 per source |

The two commands, their exit codes and their output are in
[02-data.md 2.9](02-data.md#29-running-the-whole-pipeline). The Python library `pgvector`
gives SQLAlchemy the `vector` column type; the database extension of the same name is on
since migration 0001.

### The model and the cost of a call

The embedding model is the setting `LLM_EMBEDDING_MODEL` and its price is
`LLM_EMBEDDING_USD_PER_MTOK` (3.8). The model name is never written in the code. The model
used so far is `text-embedding-3-small`: 1,536 numbers per vector, $0.02 per million
tokens (read on the provider's pricing page on 2026-10-10).

How a call is budget-checked and recorded is in 3.8, "Embedding calls". In short: the
highest possible cost (the byte length of the texts times the price) must fit the month's
budget or the call is refused; each request writes one `llm_calls` row with
`output_tokens` 0; there is no fallback.

Two purposes are stored in `llm_calls.purpose`:

| Purpose | Written by |
|---|---|
| `evidence_embedding` | The embedding run, one row per batch of chunks |
| `query_embedding` | A search by text, one row per question |

### What text is embedded

A chunk on its own can be one short sentence, such as "This procedure is covered under
Medicare.", which says nothing without the document it comes from. So the embedded text is
built by `build_embedding_input` from three parts, joined by a blank line and with no
labels:

1. the document's title,
2. the chunk's section title, when it has one,
3. the chunk's text.

The function is pure: the same chunk always gives the same text. The stored chunk `text`
and its `text_sha256` are not changed, so a citation still points at the chunk text alone.

`EMBEDDING_INPUT_VERSION` (`v1`) names this recipe. Vectors made from two recipes cannot be
compared, so a change to the recipe needs a new version and a new embedding run. The
version is stored with every vector and, as `prompt_version`, with every spend row.

A question is embedded as it is, with no title in front of it.

### The table

`evidence_chunk_embeddings` has one row per chunk, model and input version. It is a public
reference table: made from public policy text only, no `account_id`.

| Column | Holds |
|---|---|
| `evidence_chunk_id` | The chunk the vector belongs to; removing a chunk removes its vectors |
| `model_name` | The model that made the vector; never empty |
| `input_version` | The recipe that built the embedded text; never empty |
| `embedding` | The vector, type `vector(1536)`; the database refuses another size, a NaN and an infinity |
| `created_at`, `updated_at` | As on every table |

The three first columns are unique together. The table has no index besides that key's
(see "How search ranks and filters").

The vectors are in their own table, not in a column of `evidence_chunks`, for two reasons.
The model name and the input version are stored with every vector, and a second model can
be tried without touching the chunks.

The size 1,536 is the constant `EMBEDDING_DIMENSIONS`. It is fixed in the column type, so a
model with another vector size needs a new migration.

### The embedding run

`run_evidence_embedding` embeds every stored chunk that has no vector for the model and
the input version:

1. It takes a lock in the database (see below). If another run holds it, it stops here.
2. It reads the chunks without a vector, ordered by source, `source_document_id` and
   `chunk_index`, and builds the text of each.
3. It sends them in batches of 100 texts (`EMBEDDING_BATCH_SIZE`), one gateway call per
   batch.
4. It checks each answer, stores the batch's vectors and commits, before the next batch
   is sent.

A chunk that already has a vector is skipped. So a second run embeds nothing and spends
nothing, and a run that stopped half way is continued, not paid for twice.

The run ends early, without a traceback, in these cases. Nothing of the failing batch is
stored. The batches finished before are kept, with one exception that follows the table:

| Case | Paid for |
|---|---|
| The call does not fit the monthly budget | no |
| The provider cannot answer | no |
| The provider refuses the request (a bad request, a wrong key) | no |
| The answer has another number of vectors than texts | yes |
| The answer comes from another model than the configured one | yes |
| A vector is not 1,536 finite numbers | yes |
| A chunk of the batch is gone at the write (the corpus was loaded during the run) | yes |
| Another embedding run is in progress | no, nothing is called |

The size of a vector is checked in the code before the write, so a wrong size gives a plain
message and not a database error.

The exception is the corpus loaded during the run. The lock below stops a second embedding
run, not the loader. A load removes every chunk and, with the chunks, every vector, also
the ones this run stored in its earlier batches. The run then counts the chunks and the
vectors again, so its summary says what is in the database now.

**Only one run at a time.** Two runs at once would both see the same chunks as not embedded
and both pay for them. A run therefore takes a Postgres advisory lock (a named lock the
database keeps for one connection; the key is `EMBEDDING_RUN_LOCK_KEY`) and does not wait
for it: a second run started meanwhile stops before it calls the provider, spends nothing
and the command exits with 1. The database drops the lock when the connection closes, so a
run that is killed does not leave it behind.

### How search ranks and filters

`src/rag/retrieval.py` has two functions with the same filters and the same order:

| Function | Takes | Model call |
|---|---|---|
| `search_evidence_by_vector(session, query_vector, *, model_name, ...)` | A vector | None. It only reads the database |
| `search_evidence(session, gateway, query_text, *, model_name, ...)` | A question as text | One paid embedding call through the gateway, then the first function |

**Ranking.** The measure is cosine distance, which compares the direction of two vectors
and ignores their length. The score of a result is the cosine similarity, 1 minus the
distance: 1 means the same direction, 0 unrelated. The closest chunk comes first. Chunks
with the same score come back by `source`, then `source_document_id`, then `chunk_index`,
so the order is repeatable. A fixed tie order is needed because 33 of the 1,120 chunks of
the determinations repeat the text of a chunk in another document.

**No index.** The database compares the question with every stored vector. With 2,873 rows
that is fast and the answer is exact; a vector index would make the answer approximate.

**Filters.**

| Argument | Default | Meaning |
|---|---|---|
| `model_name` | none, required | Only vectors of this model and of `EMBEDDING_INPUT_VERSION` are compared |
| `top_k` | 5 (`DEFAULT_TOP_K`) | The most chunks returned; at least 1 |
| `source` | none | Keeps the chunks of one source (`cms_ncd` or `cms_manual`). Without it both sources are searched together, which is what the agent loop does |
| `min_similarity` | none (off) | Drops the chunks below this score; between -1 and 1 |

**What comes back when there is nothing.** An empty list means that no chunk passed the
filters. It never means that the chunks were not embedded: that case raises an error, so
"nothing was embedded" (or a new load removed the vectors) cannot be mistaken for "no
evidence fits".

| Error | Raised when |
|---|---|
| `EvidenceNotEmbeddedError` | No vector is stored for the model and input version. The search by text checks this before it spends; the search by vector checks it only when nothing came back, so a normal search costs one query |
| `QueryEmbeddingError` | The answer to a question's embedding call came from another model, or is not 1,536 finite numbers with at least one that is not zero. The call is already paid for and recorded |
| `ValueError` | `top_k` under 1, `min_similarity` outside -1 to 1, a question with no text, or a query vector that is not 1,536 finite numbers with at least one that is not zero. Nothing is spent |

The gateway's own errors (3.8) leave `search_evidence` as they are.

### What a result carries

A result is an `EvidenceMatch`, a Pydantic model:

| Field | Holds |
|---|---|
| `source`, `source_document_id`, `chunk_index`, `text_sha256` | The four values a citation names a chunk by (3.13) |
| `section_number` | The number a reader would cite, such as `220.6.17` or, for a manual section, `100-04-29-310.2` |
| `document_title`, `section_title` | The titles; the section title may be missing |
| `text` | The chunk's text; left out when a result is printed |
| `similarity` | The score |

A result never carries a row id, because a new load of the corpus gives every row a new id.

### The search quality check

No file says which document answers which claim (3.13), so the search is checked with 40
hand-made questions: 20 about coverage determinations and 20 about manual sections, five
per chapter. Each names the one document a good search brings back, by its source and its
section number. `python3 -m pipelines.check_evidence_search` asks each question through
`search_evidence` with the top 5 and counts a hit when a chunk of the expected document is
among the results.

- **Both sources are searched at once**, with no `source` filter, because that is how the
  agent loop searches. A manual chunk can therefore take the place of a determination in
  the top 5, and the check would show it.
- A question names its source because a section number is unique only within a source. The
  same number in the other source is a miss.
- The command prints the share of all questions and the share per source. A line per
  source counts the questions that expect a document of that source.
- The share of hits is **recorded, not a gate**: the command exits with 0 whatever the
  share is, and the build plan has no target for it.
- The functions in `src/evaluation/search_quality.py` call no model. The command passes the
  search in as a function, so the paid call stays in the command and a test passes a stub.
- A question file that expects a section number no stored document of the named source has
  is refused before anything is spent. Otherwise a typing mistake would be counted as a
  failure of the search.
- Run the command again after a new embedding run with another model or another input
  version, and after a load of either source.

### Measured

**The embedding run**, on 2026-10-11 on the local database, after migration 0011:

| Number | Value |
|---|---|
| Chunks embedded | 1,120 of 1,120 |
| Gateway calls | 12 (11 batches of 100 and one of 20) |
| Input tokens the provider reported | 193,883 |
| Cost | $0.003884 |
| Time | 37 seconds |
| Rows in `evidence_chunk_embeddings` | 1,120, each with 1,536 numbers, all `text-embedding-3-small` / `v1` |
| Chunks without a vector | 0 |
| A second run | embedded 0, spent $0, exit 0 |

The bound worked out before the run was $0.0237 (the byte length of the texts times the
price). The real cost was about a sixth of it. All 12 answers carried usage numbers.

**The search quality check**, on 2026-10-11, the project's 20 questions, top 5:

| Number | Value |
|---|---|
| Questions that found their determination | 19 of 20 (95.00%) |
| Gateway calls | 20 |
| Cost | $0.000020 |
| Time | 21 seconds |

The one miss is question 19: it expects section 160.18 and the search returned chunks of
160.7, 160.7.1 and 160.2.

After both runs the month-to-date spend was $0.705857 of the $3.00 cap.

**The embedding run for the manual chapters**, on 2026-10-12, after the load in 3.13:

| Number | Value |
|---|---|
| Chunks embedded | 1,753 of 2,873 (the 1,120 determination chunks were skipped) |
| Gateway calls | 18 (17 batches of 100 and one of 53) |
| Input tokens the provider reported | 311,176 |
| Cost | $0.006232 |
| Time | 53 seconds |
| Rows in `evidence_chunk_embeddings` | 2,873, one per chunk |
| Manual chunks without a vector | 0 |
| A second run | embedded 0, spent $0.000000, exit 0 |

The issue allowed at most $0.10 for this run.

**The search quality check with both sources**, on 2026-10-12, the project's 40 questions,
top 5, no source filter:

| Questions that expect | Found their document |
|---|---|
| A coverage determination (`cms_ncd`) | 19 of 20 (95.00%) |
| A manual section (`cms_manual`) | 16 of 20 (80.00%) |
| All | 35 of 40 (87.50%) |

| Number | Value |
|---|---|
| Gateway calls | 40 |
| Cost | $0.000040 |
| Time | 37 seconds |

The determination questions gave the same result as before the chapters were loaded: 19 of
20, and the same miss with the same three documents returned. So for these 20 questions the
1,753 manual chunks did not push a determination out of the top 5.

The four manual misses:

| Question | Expected | Returned instead |
|---|---|---|
| 29 | `100-02-16-130` | `100-02-16-40.3`, `100-02-15-230`, `100-02-15-230.6`, `100-02-15-30`, `100-02-15-150` |
| 32 | `100-04-29-310.2` | `100-04-30-150.8`, `100-04-29-220`, `100-04-30-260.4.3`, `100-04-29-320.2`, `100-04-30-200.4.3` |
| 35 | `100-04-29-270.1` | `100-04-30-120.4`, `100-04-29-270.1.1`, `100-04-29-270.1.2`, `100-04-29-270.1.3` |
| 36 | `100-04-30-50` | `100-04-30-40.2.2`, `100-04-30-40.2.1`, `100-04-30-40.1` |

In all five misses every returned chunk came from the source the question expects. The
questions were kept as they were written; none was reworded after the run.

After these runs the month-to-date spend was $0.786894 of the $3.00 cap.

### Known limits

About what the number shows:

- The quality number comes from 40 hand-made questions, 20 per source. It shows that the
  search works at all; it does not prove the search is good.
- The questions were written from the titles of the documents (for the manual sections also
  from their opening text), and the title is part of the embedded text. A hit means the
  right document came back, not that the returned chunk answers the question.
- A hit needs the one named section. The manuals spread a topic over a section and its
  subsections, so a miss can still have found text on the topic: question 35 expects
  section 270.1 and got its subsections 270.1.1 to 270.1.3. The check does not count that.
- The questions are plain questions about a rule. They are not the question the drafting
  step builds from a claim, so the manual share says nothing about what a draft gets.
- The search shows that retrieval works. It does not show that the evidence fits real
  claims: the limits of the corpus in 3.13 all still hold.

About the vectors:

- A load of the corpus removes every vector, because it removes the chunks. Until
  `pipelines.embed_evidence_corpus` runs again, a search raises `EvidenceNotEmbeddedError`.
  Each such run pays for all chunks again.
- A vector that is all zeros and already stored would give a score that is not a number.
  The embedding run refuses a vector that is not finite numbers but does not refuse one of
  zeros. No stored vector is known to be one.
- The batch of 100 texts is a cautious pick. The largest request the provider accepts was
  not looked up.

About the budget:

- The lock stops two embedding runs. It does not cover the budget check itself: an
  embedding run and another command that calls a model at the same moment (an extraction
  run, a search) can both pass the check (3.8).
- The highest possible cost of an embedding call is loose, about six times the real cost on
  the run above. It only matters within one call of the cap.

About the search:

- Ties are ordered by `source_document_id` as text, so `10` comes before `9`. The order is
  repeatable, which is all it is for.
- With no index every search reads every vector. That is right for 2,873 rows and would
  need a second look for a much larger corpus.

## 3.15 Cited appeal drafting

Drafting turns one denied claim into an appeal letter for a person to review. It is the
third step of the evidence work: 3.13 stores the policy text, 3.14 searches it, and this
section writes a letter that may lean on what the search found.

One rule shapes everything here: **every policy statement in a draft must cite evidence
that was really retrieved, with a quote that is really in that evidence**. A function
called the citation validator checks this. A draft that fails is blocked: it is counted and
not stored. This is the hard rule "no draft released without a valid citation for every
policy statement" ([conventions 6.8](06-conventions.md#68-hard-rules)).

Every model call goes through the gateway and passes the budget check first (3.8). The
claims are the made-up claims of the synthetic sample, so no real patient data is involved.
A stored draft is never sent anywhere.

Drafting itself does not look at the rules verdict (3.11) or the win score (3.10): the
command of this section drafts for every selected denied claim. The agent loop (3.16) is
the path that runs the rules and the score first and calls `draft_appeal` only for a claim
they let through.

### Where the code is

| File | Holds |
|---|---|
| `src/drafting/schemas.py` | The draft as the model answers it (`AppealDraft`, `DraftParagraph`, `DraftCitation`), the checked letter (`AppealLetter`, `LetterParagraph`, `Citation`) and `evidence_label(position)` |
| `src/drafting/citation_validator.py` | `validate_citations(draft, matches)`, its answer `CitationCheck`, the four problem kinds and `MIN_QUOTE_CHARACTERS` |
| `src/drafting/search_question.py` | `build_search_question(category)`, `SEARCH_PHRASES`, `SEARCH_QUESTION_VERSION` and `MIN_EVIDENCE_SIMILARITY` |
| `src/drafting/placeholder_check.py` | `find_placeholder_paragraphs(draft)` and `PLACEHOLDER_PATTERN`: finds text in square brackets |
| `src/drafting/fact_check.py` | `find_fact_problems(draft, letter)`, its answer `FactProblem` and the two problem kinds: finds a dollar amount no denied line has, and "other" given as the reason |
| `src/drafting/reason_words.py` | `REASON_WORDS`: the plain words for each of the six denial reasons, as the model is given them |
| `src/drafting/prompts.py` | `load_drafting_prompt(prompt_dir, version)` and the default prompt version |
| `src/drafting/drafter.py` | `draft_appeal(gateway, prompt, letter, matches)`, its result and its five errors, and `build_user_message(letter, matches, *, prompt_version)` |
| `src/drafting/store.py` | Reads the claims that can be drafted; saves rows of `appeal_drafts` |
| `src/drafting/run.py` | `run_drafting(...)`: many claims, one at a time, each checked letter saved as it arrives |
| `src/db/models.py` | `StoredAppealDraft`, the table `appeal_drafts` |
| `src/db/migrations/versions/rev_0012_appeal_drafts.py` | Migration 0012: the table; the downgrade drops it |
| `pipelines/draft_appeals.py` | The command ([02-data.md 2.9](02-data.md#29-running-the-whole-pipeline)) |
| `config/prompts/drafting/<version>/appeal_letter.txt` | The prompt. Three versions are on disk, `v1`, `v2` and `v3`; all three can be loaded |

The database model is named `StoredAppealDraft` because `AppealDraft` is already the name
of the Pydantic shape of the model's answer.

### One claim, step by step

1. Read the extracted fields of the claim's denial letter (the `document_extractions` row
   of extraction prompt `v2`, 3.9).
2. Build the search question: the fixed phrase of the letter's denial reason.
3. Search the evidence with `search_evidence` (3.14) and keep at most 5 results, each with
   a similarity of at least 0.55 (see "The search question" below). This is one small paid
   embedding call. The search may return fewer than 5 results, or none.
4. Call the model once with the letter's values and the results. This is the paid drafting
   call. It is made also when there is no result.
5. Parse the answer into the draft shape.
6. Run the citation validator on the draft and the same results.
7. Look for placeholder text, which is text in square brackets (see "A draft that cannot be
   used" below).
8. Run the fact check: look for a dollar amount that no denied line has, and for "other"
   given as the denial's reason (see the same sub-section).
9. Turn each label into the four citation fields and store the checked letter.

Steps 4 to 8 are `draft_appeal`. Its docstring says it spends money. A failure at step 4, 5,
6, 7 or 8 ends the work on this claim: nothing is repaired and the model is not called a
second time.

### What the model is given

The user message is one JSON object with two keys:

| Key | Holds |
|---|---|
| `denial_letter` | The values of the letter extraction (claim number, dates, codes, the denied lines with their amounts and reasons, the names, the reference number, the appeal deadline). A value the extraction did not find is null. The confidence scores are left out. Which values go in depends on the prompt version; see "The two forms of the message" below |
| `evidence` | The search results in their order. Each has a `label`, the `document_title`, the `section_number`, the `section_title` and the `text` |

The label of a result is its position: `E1` is the first result, `E2` the second, up to
`E5`. `evidence_label(position)` builds it. With no result the `evidence` list is empty.

The model is **not** given the raw text of any document, the clinical note, the
prior-authorisation record, the rules verdict or the win score.

The system message is the prompt file. It is read from the folder in settings, and its
version (the constant `DRAFTING_PROMPT_VERSION`, today `v3`) is stored with every draft and
with every spend row. The prompt holds no secret. It tells the model to write a policy
paragraph only when a passage supports the appeal of this claim, and otherwise to write the
letter with `other` paragraphs only.

| Prompt version | Means |
|---|---|
| `v1` | The answer shape, the two kinds of paragraph, the quote rule and a short list of rules for the letter |
| `v2` | The `v1` text as the base, plus fourteen rules against the faults the second review found (3.17) |
| `v3` | The `v2` text as the base, plus three rules against the citation faults the third review found, and a new form of the user message. Today's version |

The fourteen rules that `v2` adds, in the four groups of the prompt file:

| Group | Rule |
|---|---|
| Kinds | A `policy` paragraph also says, in the model's own words, what the quoted rule means for this claim. A quote alone is not a paragraph. The rule gives one example: "the denial names no exclusion, and the quoted rule says that each exclusion must be analysed" |
| Evidence | The evidence was found by a search for the general rule behind the denial reason; a passage is used only when it supports the appeal of this claim |
| Evidence | A passage that is a sample letter or a sample notice is not a rule and is never cited |
| Facts | Only the values of `denial_letter` are used for names, numbers, dates, codes and amounts; a value that is null or missing is left out, never made up |
| Facts | No address block, no date line, no subject line, no text in square brackets and no blank to fill in later |
| Facts | No fact is added about the care, about what the provider did or about what anyone said |
| Facts | Each appealed line is named with its own procedure code, its own two amounts and its own reason |
| Facts | The two claim totals are called claim totals and are never given as the amount of one service line |
| Facts | A category value such as `other` is never written in the letter; the prompt gives plain words for each of the six reasons |
| Letter | The letter is written for the patient, in the first person, and ends with the patient's name as the signature |
| Letter | The opening names the payer, the claim number, the reference number, the patient and the member id |
| Letter | The letter argues against the reason the denial gives and against no other reason |
| Letter | For an `other` denial the letter says that the denial names no specific reason, asks the payer to state it, asks for the claim to be reviewed again, and cites the right to a review when a passage gives it |
| Letter | The letter does not say that the denial is justified and does not ask the payer to send documents |

The plain words for the six reasons are the ones the made-up denial letters use
(`REASON_WORDING` in `src/synth/denial_letter.py`), and a test keeps the two equal.
Prompt `v2` lists them in the prompt file. From `v3` they are in code and go into the user
message (see below).

**The rule for an `other` denial was written to fit how the drafts are judged.** It was added on
2026-10-12, after the reading of the rubric for an `other` letter had been fixed (3.17) and
before any draft was written with `v2`. So the prompt tells the model to write the very
letter that reading accepts. The `v2` file was changed in place for it, because no `v2`
draft existed yet.

**The example in the first rule is the argument most accepted letters make.** It was
written after the trial searches had shown the passage it describes (the manual's sentence
that each payment exclusion must be analysed). All 10 accepted `noncovered` letters of the
third review use that sentence in that way (3.17). So that argument is the prompt's, not
one the drafting model found on its own.

Issue #53 planned ten rules. The count here is fourteen because it is taken from the
prompt file: the three letter rules on the voice, the opening and what the letter must not
say are counted one by one, and the rule for an `other` denial was added later.

A prompt rule only asks. Of the fourteen, one is also checked in code: text in square
brackets. Two more, on claim totals and on category words, are checked in part since the
fact check (see below).

**What `v3` changes.** The third review rejected 8 of 30 drafts for a wrong fact and 4 for a
citation that does not fit (3.17). Prompt `v3` adds three rules for the citation faults:

| Group | Rule added by `v3` |
|---|---|
| Kinds | Say nothing about a rule that the quote itself does not say. Do not call the denial, the notice or the payer's decision improper, wrong or against the rules unless the quote says so |
| Evidence | Never cite a rule that supports the denial. The rule gives one example: a passage that says a service which is not reasonable and necessary is not covered |
| Evidence | When no passage supports the appeal, write no `policy` paragraph (`v2` said this in softer words: "write the letter with `other` paragraphs only") |

Three rules of `v2` are reworded, because the message no longer holds what they spoke of:

| Rule of `v2` | In `v3` |
|---|---|
| The two claim totals are called claim totals and are never given as a line amount | `denial_letter` holds no amount for the whole claim; write no claim total and no sum of several lines |
| A category value is never written; the prompt lists plain words for the six reasons | `denial_reason` and each line's `reason` give the reason in plain words; say it in these words, never as a single word or the name of a category. The list is gone from the prompt |
| The rule for an `other` denial starts "when `denial_reason_category` is `other`" | It starts "when `denial_reason` is 'the service could not be paid as submitted'". What the letter must then say is unchanged |

**The two forms of the message.** `build_user_message(letter, matches, *, prompt_version)`
builds the user message, and the prompt version decides which form it has:

| | Prompts `v1` and `v2` | Prompt `v3` and every later one |
|---|---|---|
| Values of `denial_letter` | all 15, as they are stored | 13: `total_allowed_charge_amount` and `total_payment_amount` are left out |
| The denial's reason | the key `denial_reason_category` with a category value, such as `other` | the key `denial_reason` with plain words, such as "the service could not be paid as submitted" |
| The reason of each denied line | the key `reason_category` with a category value | the key `reason` with plain words |
| A reason the extraction did not find | null | null |

The versions that get the first form are the named constant `FULL_VALUE_MESSAGE_VERSIONS`.
The plain words are the named constant `REASON_WORDS`. So the same letter drafted with
`v2` again gets the message `v2` was written for.

**Why the message changed and not only the prompt.** In the third review the drafting model
gave a claim total as the amount of a line, and wrote the word `other` as the reason,
although prompt `v2` has a rule against each. A model cannot copy a value it is never
sent. So from `v3` the totals and the category values are not in the message at all. The
`evidence` key is the same in both forms.

A prompt rule only asks. Of the three rules `v3` adds, **none is checked in code**: no code
knows whether a paragraph says more than its quote, or whether a passage supports the
denial. The two fact rules are checked in part by the fact check (see "A draft that cannot
be used").

Every drafting call is recorded in `llm_calls` with the purpose `drafting`. The output
limit is 1,500 tokens (`DRAFTING_MAX_OUTPUT_TOKENS`).

### The search question

A denial letter holds a reason category and procedure codes. It holds no words about the
service. So the question is one fixed phrase for the category, and nothing else:

| Denial reason | The question | Since |
|---|---|---|
| `noncovered` | services excluded from Medicare coverage and the exceptions to the exclusions | `v3` |
| `medical_necessity` | services that are not reasonable and necessary for the diagnosis or treatment of illness or injury and limitation on liability for such a denial | `v3` |
| `other` | how to appeal a denied claim: the right to request a redetermination of an initial determination | `v3` |
| `duplicate` | duplicate claim for the same service | `v1` |
| `benefits_exhausted` | frequency limits and the number of covered services | `v1` |
| `coordination_of_benefits` | Medicare as secondary payer and coverage by another insurer | `v1` |

The phrases are the named constant `SEARCH_PHRASES`. A letter whose reason is unknown gets
the phrase of `other`.

`build_search_question(category)` is pure: no model call, no database.

**The phrases ask for a general rule, not for the service.** The three new ones were written
to reach the CMS manual chapters (3.13): what is excluded and when an exclusion does not
apply, what "not reasonable and necessary" means, and the right to a second look. So
**every claim with the same denial reason asks the same question and gets the same
passages.**

**The procedure codes are no longer in the question.** Up to `v2` the codes of the denied
lines followed the phrase. A code tells the search nothing: 0 of the 66 distinct codes of
the 55 claims measured on 2026-10-11 appear in the evidence text. In the trial searches of
2026-10-12, adding one claim's codes to a phrase lowered its best similarity by 0.04 to
0.08.

**The minimum similarity.** The search always has a best match, also when nothing in the
corpus is about the claim. So the caller passes `MIN_EVIDENCE_SIMILARITY` (0.55) to
`search_evidence` as `min_similarity` (3.14). A match with a lower score is left out: the
model never sees it, so it cannot cite it. The number is one named constant; no call site
holds it. Both callers pass it: the agent loop (3.16) and the command of this section.

**A claim with no match is still drafted.** The model is called with an empty evidence
list, and the prompt tells it to write `other` paragraphs only. Such a letter has no
citation, passes the validator and is stored. With search question `v2` this was 41 of the
56 drafted claims; with `v3` it was none of the 54 (3.17).

`SEARCH_QUESTION_VERSION` names how the search is asked: the phrases, what else goes into
the question, and the minimum. A change to any of them changes what a search finds, so it
needs a new version. The version is stored with every draft.

| Version | Means |
|---|---|
| `v1` | The six first phrases and the codes; no minimum, the top 5 results whatever their score |
| `v2` | The same question text; a result must have a similarity of at least 0.55 |
| `v3` | New phrases for `noncovered`, `medical_necessity` and `other`; no procedure codes; the minimum stays 0.55. Today's version |

**How the three new phrases were chosen.** From 26 trial searches on 2026-10-12 (26 query
embeddings, $0.000026), against the two evidence sources together. They were picked, not
tuned: no set of questions with a known right answer was used.

| Denial reason | Best similarity of the new phrase | Of the phrase before it |
|---|---|---|
| `other` | 0.7124 | 0.5108, below the minimum, so no evidence |
| `noncovered` | 0.6966 | 0.6000 |
| `medical_necessity` | 0.6615 | 0.6634, but its best match was a coverage determination that does not fit |
| `coordination_of_benefits` (phrase unchanged) | 0.8044 | the same |
| `duplicate` (phrase unchanged) | 0.3895, nothing reaches the minimum | the same |
| `benefits_exhausted` (phrase unchanged) | 0.5129, nothing reaches the minimum | the same |

These were measured without procedure codes.

The value 0.55 is a **judgement**, not a measurement of what fits. It was set on 2026-10-11
from two small sets of scores, which overlap:

| Set | Similarity | Kept at 0.55 |
|---|---|---|
| The best match of each of the 55 claims the loop had drafted for | 0.4219 to 0.6634, median 0.5309 | 14 of 55 claims keep any match |
| The expected determination of the 19 correct hits of the search check (3.14) | 0.5224 to 0.7802, median 0.6213 | 17 of 19 |

Of the 28 citations in the 55 drafts of search question `v1`, 10 had a similarity of at
least 0.55. Both sets were measured with the `v1` question text, before the manual chapters
were loaded; the minimum was not looked at again for `v3`.

### The output shape

The model answers with one JSON object, a list of paragraphs. The answer is parsed into the
Pydantic model `AppealDraft`:

| Field | Holds |
|---|---|
| `kind` | `policy` or `other` |
| `text` | The paragraph as it stands in the letter |
| `citations` | A list of `label` + `quote`. Only a `policy` paragraph may have one |

A paragraph is `policy` when it says anything about what a coverage policy, a
determination, a rule or a regulation says, allows, requires or covers. Everything else is
`other`: the opening, the facts of the claim, the request, the closing.

The shape itself refuses three things, before the validator runs: an extra key, a citation
on an `other` paragraph, and a label that is not `E` followed by a number from 1 to 99.

**The model writes labels and quotes only.** It never writes the four values that name a
chunk. After the validator has passed the draft, the code looks each label up in the search
results and fills `source`, `source_document_id`, `chunk_index` and `text_sha256` (3.13).
The result is an `AppealLetter`. A citation in it never holds a row id.

### The citation validator

`validate_citations(draft, matches)` is a pure function: no model call, no database, no
clock. The same draft and the same results always give the same answer. `matches` must be
the same list, in the same order, that the model was shown, because a label is a position
in it.

A draft passes only when every `policy` paragraph:

1. has at least one citation;
2. cites only labels that are among the search results;
3. gives a quote of at least 20 characters (`MIN_QUOTE_CHARACTERS`) that is found word for
   word in the text of the cited chunk.

Whitespace is the only difference a quote may have: runs of spaces and line breaks count as
one space, and the length is measured after that. Everything else is exact. Upper and lower
case differ, and a curly quotation mark is not a straight one, so a quote the model tidied
up is blocked. A quote must also start and end where a word does: "covered at home" is not
found in "uncovered at home", because that quote would say the opposite of the text. A
hyphen, an apostrophe, a point or a comma with a letter or digit on both sides is part of
the word or number, so "covered at home" is not found in "non-covered at home" and "200
units" is not found in "1,200 units". The minimum length is there because a few words such
as "is covered" are in almost every chunk and would prove nothing.

The answer is a `CitationCheck` with every problem found, not only the first. Each problem
has a kind, the paragraph's position and the label. It never holds quote or chunk text.

| Problem kind | Means |
|---|---|
| `policy_without_citation` | A `policy` paragraph has no citation |
| `unknown_label` | A cited label is not among the search results |
| `quote_too_short` | A quote is under 20 characters |
| `quote_not_in_evidence` | A quote is not in the text of the cited chunk |

A draft with only `other` paragraphs passes: it makes no policy statement, so there is
nothing to cite.

### A draft that cannot be used

`draft_appeal` returns a checked letter or raises one of five errors:

| Error | Raised when |
|---|---|
| `DraftTruncatedError` | The answer reached the output limit before it was complete |
| `DraftParseError` | The answer is not JSON, or does not fit the draft shape |
| `DraftBlockedError` | The validator found a problem; `problems` holds every one |
| `DraftPlaceholderError` | A paragraph holds text in square brackets; the error holds the positions of those paragraphs |
| `DraftFactError` | The fact check found a wrong fact; `problems` holds every one, each with its kind and the position of its paragraph |

In all five cases the call is already paid for and recorded, **nothing is stored**, and
there is no repair and no second call. The run counts the case and goes on to the next
claim. The gateway's own errors (over budget, no provider available) pass through.

No error message quotes the model's answer, a quote or evidence text. The parser's own
error would quote the answer, so it is dropped (`raise ... from None`).

**The placeholder check.** A model that lacks a value sometimes writes a blank for a
person to fill in, such as `[Your Name]`. In the second review 11 of the 30 notes named
one (3.17). Such a letter is not ready for review, so `find_placeholder_paragraphs`
refuses it. A placeholder is an opening square bracket with a closing one later in the
`text` of the same paragraph, whatever is between them. The check is pure: no model call,
no database.

- Only the paragraph text is looked at. A `quote` is copied evidence text, and the
  validator already compares it with its chunk.
- The check runs **after** the citation validator. A draft with both faults is counted as
  blocked, not as a placeholder.
- The drafting command counts the case in its own report line,
  `refused for placeholder text: N`. The agent loop records it as the drafting failure
  `placeholder_text` (3.16).
- The error holds paragraph positions only, never the text.

Prompt `v2` also tells the model to write no text in square brackets. The check is there
because a prompt rule only asks.

**The fact check.** In the third review 4 of the 30 drafts were rejected for one of two
wrong facts: a claim total given as an amount of the appeal, or the category word `other`
given as the denial's reason (3.17). `find_fact_problems(draft, letter)` refuses a draft
with either. `letter` is the extracted denial letter the draft was written from. The check
is pure: no model call, no database.

| Problem kind | A paragraph is refused when |
|---|---|
| `amount_not_a_denied_line` | it holds a dollar amount that is not the allowed amount or the paid amount of one of the letter's denied lines |
| `other_as_reason` | it gives the word "other" as the reason of the denial |

- **A dollar amount** is a dollar sign followed by a number (`DOLLAR_AMOUNT_PATTERN`).
  Amounts are compared by value, so `$70` equals a stored `70.00`, and `$1,250.50` is read
  with its thousands separator. The number is read whole: one written another way, such as
  `$70,50` or `$70k`, is refused, also when it starts like a line's amount. When the letter
  has no denied line, every dollar amount is refused.
- **"Other" as the reason** is found by a fixed list of wordings
  (`OTHER_AS_REASON_PATTERNS`): the word alone in quotation marks, straight or curly, and
  phrases such as "the category of other" or "the reason was other", where the word ends
  the phrase. "A reason other than this one" and "other coverage" pass.
- Only the paragraph text is looked at, never a `quote`: a dollar amount or the word
  "other" inside quoted evidence refuses nothing. A test proves it.
- The check runs **last**, after the citation validator and the placeholder check. A draft
  with an earlier fault is counted under that fault.
- The drafting command counts the case in its own report line,
  `refused by the fact check: N`. The agent loop records it as the drafting failure
  `fact_check` (3.16). The trace stores only that value, not the kinds of problem.
- A `FactProblem` holds a kind and a paragraph position, never the text. The error message
  gives a count per kind.

The word `noncovered` is **not** checked. In the third review one accepted letter wrote it
for the reason, and it is close to the plain words "not covered".

### The table

`appeal_drafts` has one row per claim and drafting prompt version. It is a public reference
table with no `account_id`: it holds only drafts for the made-up claims of the synthetic
sample, like `document_extractions` (3.9). A customer's draft will get its own business
table with `account_id` (the `drafts` table of 3.4, not built yet).

| Column | Holds |
|---|---|
| `source_claim_id` | The claim, a link to `claim_samples`; removing a claim removes its drafts |
| `prompt_version` | The drafting prompt version; never empty |
| `search_question_version` | The version of the search question; never empty |
| `model_name` | The model that answered; never empty |
| `provider` | The role that answered: `primary` or `fallback` |
| `text_sha256` | The hash of the denial letter document the draft was built from |
| `letter` | The checked `AppealLetter` as JSON: each paragraph's kind and text, and per citation the four citation fields and the quote |
| `created_at`, `updated_at` | As on every table |

`source_claim_id` and `prompt_version` are unique together, so a new prompt version keeps
the old drafts.

**No foreign key points at the evidence tables.** A new load of the corpus gives every
chunk a new row id (3.13), and it must not remove a draft. A citation names its chunk by
the four values instead.

The table does not check what is inside `letter`. The only code that writes it is
`save_appeal_draft`, which is given what `draft_appeal` returned. A test proves that a
blocked draft leaves no row.

### A run over many claims

`run_drafting` takes the claims the command selected and works through them one at a time.

**Which claims are selected.** A claim is selected when it is denied, it is in the chosen
split, and its denial letter has a current extraction: a `document_extractions` row whose
hash equals the hash of the letter text stored now. The order is claim id, so the same
`--limit` always means the same claims. The command prints how many denied claims of the
split were left out for having no current extraction.

**Which are skipped.** A claim that already has a draft of the prompt version is skipped
and costs nothing. Each letter is committed on its own, so a run that stopped keeps what it
finished and the same command continues it.

**A claim with no usable draft is tried again by the next run.** Nothing is stored for a
blocked draft, a draft with placeholder text, a draft the fact check refused or an unusable
answer, so the next run sees the claim as not drafted and pays for one more try. A run drafts 0 and spends $0 only once every selected claim has a draft.

The run ends early, without a traceback, in these cases. What was finished before is kept:

| Case | Why the run stops |
|---|---|
| The monthly budget is reached | Every further call would be refused |
| No provider can answer | The next claim would most likely fail the same way |
| A provider refuses a request (a bad request, a wrong key) | The same |
| The evidence has no stored vector | Nothing can be searched until the embedding run (3.14) |
| The answer to a search question's embedding call cannot be used | A wrong model or vector size would repeat on every claim, each time paid for |

The command's report gives the counts, the time and the money. It prints no letter text and
no document text.

### Measured

Measured on 2026-10-11 on the local database, after migration 0012: the test split,
`--limit 50`, drafting prompt `v1`, search question `v1`, model `gpt-4o-mini`. The split
has 759 denied claims; 749 have a current letter extraction and 10 were left out.

These are the numbers of search question `v1` and drafting prompt `v1`, **before the
minimum similarity, the new phrases, prompt `v2`, prompt `v3` and the fact check**. The
command of this section has not been run since. The agent loop has, with search question
`v2` and with `v3`, and with drafting prompts `v1`, `v2` and `v3`; those numbers are in
3.17.

The fact check was run twice on real drafts:

| Drafts | Refused | By kind |
|---|---|---|
| The 54 stored drafts of prompt `v2`, read-only on 2026-10-12, before prompt `v3` existed | 11 | `amount_not_a_denied_line` 6, `other_as_reason` 5, none with both |
| The drafts of the agent `v4` runs (prompt `v3`, the new message), 2026-10-12 | 0 | |

The 0 says the new message works: a model that is not sent the totals and the category
values did not write them. It does not say the check is never needed.

| Run | Drafted | Blocked | Failed to parse | Cost | Time |
|---|---|---|---|---|---|
| 1 | 46 | 1 (`quote_not_in_evidence`) | 3 | $0.024261 | 235 seconds |
| 2 | the 4 left over | 0 | 0 | $0.001811 | not recorded |
| 3 | 0 | 0 | 0 | $0 | not recorded |

In run 1, stored drafts + blocked drafts + other failures = 46 + 1 + 3 = 50.

| Number | Value |
|---|---|
| Drafts stored after the three runs | 50 |
| Stored drafts with at least one citation | 15 |
| Citations in them | 15, to 5 distinct determinations |
| Citations whose four values name a chunk in `evidence_chunks` | 15 of 15 |
| Answered by the fallback | 0 |
| Cost of the three runs | $0.026072 |
| Cost of one draft | about $0.0005 (about 2,080 input and 280 output tokens) |
| Month-to-date spend after the runs | $0.731929 of the $3.00 cap |

The citation check in the fourth row was a one-off read-only query, not a command.

Stored drafts with a citation, by the letter's denial reason:

| Denial reason | With a citation |
|---|---|
| `other` | 7 of 27 |
| `noncovered` | 6 of 16 |
| `medical_necessity` | 2 of 2 |
| `coordination_of_benefits` | 0 of 4 |
| `duplicate` | 0 of 1 |

**35 of the 50 stored letters make no policy statement.** They are letters of `other`
paragraphs only: the facts of the claim and a request to review it. That is what the prompt
asks for when no passage fits, and it was the expected result of the `v1` search question,
which had no words the evidence could match.

The share of drafts a reviewer accepts (the 60% target of Stage 4) is **not** measured
here. It is measured by the draft acceptance review (see 3.17).

### Known limits

About the evidence a draft gets:

- **Every claim with the same denial reason gets the same passages.** The question is one
  fixed phrase per reason and holds nothing about the claim. In the measured run of search
  question `v3` the 60 citations of 54 drafts name only 8 manual sections, and two sections
  hold 48 of the 60. In the agent `v4` run the 135 citations of 106 drafts name 9 sections,
  and three sections hold 120 of the 135 (3.17).
- **A letter argues a general rule, never the service itself.** No passage is about the
  denied service, and the search does not look for one. The coverage determinations
  (`cms_ncd`) are still searched, but no draft of the agent `v3` and `v4` runs cites one.
- `duplicate` and `benefits_exhausted` claims get no passage: their phrases are unchanged
  and reach 0.3895 and 0.5129, below the minimum. Such a claim is drafted with no evidence.
  This was seen once: one `benefits_exhausted` claim of the agent `v4` run got no passage,
  and its letter is the only one of the 106 with no citation. No drafted claim had the
  reason `duplicate`.
- A claim whose lines have several denial reasons is searched by its headline reason only.
  8 of the 56 claims drafted in the second run have such lines.
- The three new phrases were chosen from 26 trial searches. They were not tuned, and no set
  of questions with a known answer measures them.
- The manual chapters hold sample letters and sample notices, which read like rules. Only a
  prompt rule keeps the model from citing one; no code checks it.
- The minimum of 0.55 is a judgement from 19 hand-made questions and 55 claims, measured
  with the `v1` question. The two score ranges overlap, so no value separates a passage that
  fits from one that does not.
- A correct passage below the minimum is lost: 2 of the 19 correct hits of the search check
  score under 0.55.
- A similarity above the minimum does not prove a fit. In the second review all 6 sampled
  drafts with a citation quoted a determination that was not about the denied service, and
  in the third review 4 of 30 drafts were rejected because the quote does not support its
  sentence. In the fourth review, with the three citation rules of prompt `v3`, it was 7 of
  30 (3.17). Those are verdicts of a model (Claude), not of a person. No code checks
  that a citation fits; the reviewer judges it.
- The policy text is the 2026 version and the claims are from 2008 to 2010. The letters
  name made-up payers, while the evidence is Medicare policy: a letter may speak of a
  "redetermination" or of "the MAC" to a payer that is not Medicare. The drafts show the
  mechanism (retrieve, cite, check); they are not real appeals.

About the placeholder check:

- A correct paragraph that holds a pair of square brackets is refused too, for example one
  that repeats a bracketed note from the denial letter. This was not seen in the measured
  run: 0 drafts were refused.
- A single bracket with no partner is not caught, and neither is a blank written another
  way, such as a row of underscores or "(your name)".
- Eleven of the fourteen rules of prompt `v2` are not checked by any code, and two more (on
  claim totals and on category words) only in part, by the fact check. The third review, before
  that check, still found a category word, a claim total given as a line amount and a wrong
  denial reason in the drafts (3.17).

About prompt `v3` and the fact check:

- The three rules `v3` adds are only asked of the model. The fourth review still rejected
  7 of 30 drafts because a paragraph says more than its quote or cites a rule that supports
  the denial (3.17).
- The fact check sees an amount only when it has a dollar sign. "70 dollars" or a bare
  "70.00" passes. None of the 54 drafts of prompt `v2` wrote an amount that way.
- A correct sum of several denied lines is refused, because it is no line's own amount. So
  is a claim total, also when it is the right total, and so is a dollar amount in a correct
  sentence about something else, such as a fee.
- An amount of the wrong line passes. The check knows that an amount belongs to some denied
  line, not which line the sentence is about.
- "Other" as the reason is found by a fixed list of wordings. Without quotation marks it
  is found only at the end of a phrase: "the category of other was given" passes.
- The word "other" alone in quotation marks is refused wherever it stands, also when it is
  not given as the reason. Such a draft is not stored and the next run tries the claim again.
- The check compares the draft with the **extracted** letter, not with the letter
  document. A value the extraction read wrongly is taken as right. 4 of the 8 wrong facts
  of the third review came from the extraction (a reason read as `medical_necessity`, a
  wrong line number, a damaged name), and no check on a draft can see those.
- No other fact is checked: not a name, a claim number, a date, a procedure code, a line
  number or which reason belongs to which line.

About what the validator proves:

- It checks that a quote is in the cited text. It does **not** check that the text is the
  right policy for the claim, or that the paragraph says what the quote says.
- A policy statement in a paragraph the model marked `other` is not caught. The prompt asks
  the model not to do this; nothing checks it. This is why a person reviews every draft.
- The match is exact apart from whitespace. A correct quote with a changed capital letter
  or quotation mark is blocked, and so is a quote that starts or ends in the middle of a
  word, a hyphenated word ("term care" out of "long-term care") or a number. That is the
  safe side, and it costs some good drafts.

About stored drafts:

- A draft is not redone when its letter document changes. The claim is skipped as already
  drafted. The draft can be found as out of date, because its `text_sha256` no longer
  equals the stored letter's, but nothing marks it.
- A draft is not redone when the search question version changes. The drafting command
  skips a claim that has a draft for the drafting prompt version, so such a draft keeps the
  evidence of the version it was written with. The row's `search_question_version` shows
  which version that was. Only the agent loop taking the claim again replaces it; a new
  drafting prompt version writes a new draft beside it. That is what prompts `v2` and `v3`
  did: `appeal_drafts` now holds 106 drafts of prompt `v3` and 54 of prompt `v2` (both
  search question `v3`) beside 79 of prompt `v1` (56 of search question `v2`, 23 of `v1`).
- A draft is not marked when the evidence corpus is loaded again. A citation whose
  `text_sha256` no longer matches a stored chunk is out of date; nothing looks for it.
- A claim whose draft is blocked, refused or unusable is paid for again on every run until
  it gets a draft. Nothing limits the number of tries.

About the run that was measured:

- Why the 3 answers of run 1 failed to parse was not looked into. All 3 claims got a draft
  on the next try.
- The exit code of run 1 was not captured. Runs 2 and 3 exited with 0.
- The per-kind counts in the report count drafts, not problems: a draft with two kinds of
  problem is counted under both, and one with the same kind twice is counted once.
- Every answer came from the primary model. Whether the fallback model can fill this output
  shape was not tried.
- The output limit of 1,500 tokens is an estimate. No answer of the run reached it.

About the budget:

- Two processes that call a model at the same moment can both pass the budget check (3.8).

## 3.16 Agent loop

The agent loop joins the parts of 3.9 to 3.15 into one path for one denied claim. It reads
the stored extraction of the claim's denial letter, runs the rules, scores the claim,
searches the evidence and drafts a cited appeal letter. It stores a trace of every step, so
a person can see why a claim got a draft or why it did not.

Three things hold for every run:

- **The order is fixed in code.** The five steps always run in the same order, each at most
  once. Since agent `v5` the drafting step alone may ask the model a second time (below). No
  model picks the next step and no model calls a tool.
- **Every run ends in a stop for a person.** No end state means "approved" or "sent", and
  no code path sends anything outside the database. This is the hard rule "nothing sent to
  an insurer automatically" ([conventions 6.8](06-conventions.md#68-hard-rules)).
- **Money is spent only after the rules and the score let a claim through.** One claim
  makes at most one embedding call and two drafting calls (`MAX_DRAFT_ATTEMPTS`), all
  through the gateway and its budget check (3.8). A claim that stops earlier costs nothing.

The claims are the made-up claims of the synthetic sample, so no real patient data is
involved. An uploaded letter goes through the same loop inside its job (see 3.19).

### Where the code is

| File | Holds |
|---|---|
| `src/agent/trace.py` | The trace types: `AgentTrace`, the five step models, `StepStatus`, `DraftFailure`, `EvidenceReference` and `AGENT_VERSION` |
| `src/agent/loop.py` | `run_agent_for_claim(...)`, the loop for one letter; `LoopInput`, what it reads; `AgentOutcome`, what it returns; `AgentCandidate`, a claim of the sample |
| `src/agent/store.py` | `select_agent_candidates` (the claims that can be taken), `read_finished_claim_ids`, `save_agent_run` and `ClaimRowsError` |
| `src/agent/run.py` | `run_agent(...)`: many claims, one at a time, each trace and its checked letter saved as they arrive; its answer `AgentRunSummary` |
| `src/db/models.py` | `AgentRun`, the table `agent_runs`, and `EndState`, the five end states |
| `src/db/migrations/versions/rev_0013_agent_runs.py` | Migration 0013: the table and the type `agent_end_state`; the downgrade drops both |
| `pipelines/run_agent.py` | The command ([02-data.md 2.9](02-data.md#29-running-the-whole-pipeline)) |

`src/agent/` calls only the public functions of the other packages: `evaluate_rules`
(3.11), `predict_win_probability` (3.10), `build_search_question`, `draft_appeal` and
`save_appeal_draft` (3.15) and `search_evidence` (3.14). It changes none of them. The loop
itself stores nothing; `save_appeal_draft` is called by `run_agent`.

`EndState` is in the models file and not beside the trace types, because the table stores
it as a column.

### One claim, step by step

| Step | What it does | Costs money |
|---|---|---|
| 1. Extraction | Reads the claim's stored letter extraction (the `document_extractions` row of extraction prompt `v2`, 3.9). It makes **no** extraction call | no |
| 2. Rules | Calls `evaluate_rules` with the letter date, the appeal deadline and the total allowed charge from the extraction, and with "today" and the amount floor passed in | no |
| 3. Scoring | Scores the claim with the saved win model. For a claim of the sample the three features come from its stored rows through `build_claim_features` (`FEATURE_VERSION` `v1`), not from the letter; for an upload they come from the letter (3.19). Without features the loop stops here as `needs_review_no_draft`, with no score | no |
| 4. Retrieval | Builds the search question with `build_search_question` (the fixed phrase of the letter's denial reason, no procedure codes) and calls `search_evidence` with `min_similarity=MIN_EVIDENCE_SIMILARITY` (0.55, see 3.15): at most 5 results, possibly none | one small embedding call |
| 5. Drafting | Calls `draft_appeal` with the letter's values and the results. A checked letter is returned with the trace | one model call |

`run_agent_for_claim` takes anything that fits `LoopInput`: the extracted letter, the hash of
the letter text, the extraction prompt version and the features (or None). An
`AgentCandidate` of the sample fits it, and so does the `UploadLetter` of a job (3.19). It
returns an `AgentOutcome`, the trace and the checked letter (None when there is none), and
stores neither. The caller stores both: `run_agent` puts the letter in `appeal_drafts` and the
trace in `agent_runs`, in one commit; a job puts them in `upload_drafts` and
`upload_agent_runs`.

"Today", the amount floor and the threshold are arguments. Nothing in `src/agent/` reads
the clock or the settings.

The loop **always drafts fresh**. A letter that passes the citation validator replaces the
draft the claim already had for the same drafting prompt version, so a `needs_review` trace
always describes the draft that is stored.

**A claim whose search returns no match goes on to drafting.** The retrieval step is `done`
with an empty list of results, the model is called with no evidence, and a checked letter
with no citation ends as `needs_review`. There is no status and no end state of its own for
"no evidence". A test proves it.

### What stops the loop

| After step | When | End state | What is stored |
|---|---|---|---|
| Rules | The verdict is `hard_fail` | `stopped_by_rules` | The trace |
| Rules | The verdict is `must_review` | `needs_review_no_draft` | The trace |
| Rules | The rules give no verdict (the extracted amount is below zero) | `draft_failed` | The trace |
| Scoring | There are no features (an upload whose letter lacks a value the model needs, 3.19) | `needs_review_no_draft` | The trace; scoring `stopped` with no score |
| Scoring | The score is below the threshold | `below_threshold` | The trace |
| Drafting | The draft is blocked by the validator, cut off at the output limit, cannot be parsed, holds text in square brackets, or is refused by the fact check (a blocked or unparseable draft only after its second attempt) | `draft_failed` | The trace; no draft |
| Drafting | The letter passed the validator and the two checks after it | `needs_review` | The trace and the draft |

Only a `pass` verdict goes on to scoring. A score **equal** to the threshold goes on. For
the first four rows no model call is made and no draft is stored; tests prove both with a
gateway that fails when it is called.

The five end states, in the order of the type `agent_end_state`:

| End state | Means |
|---|---|
| `needs_review` | A checked draft is stored and waits for a person |
| `needs_review_no_draft` | The rules sent the claim to a person; there is no draft |
| `stopped_by_rules` | The rules closed the claim: the deadline has passed or the amount is below the floor |
| `below_threshold` | The model gives the appeal too small a chance to draft for |
| `draft_failed` | A step could not give an answer for this claim; there is no draft |

The first four are final for an agent version. `draft_failed` is not: the next run takes
the claim again.

### The trace

`AgentTrace` is a Pydantic model with the end state, the agent version and one entry per
step. Every step has a status:

| Status | Means |
|---|---|
| `done` | The step ran and the loop went on |
| `stopped` | The step ran and its answer ended the loop |
| `failed` | The step could not give an answer for this claim |
| `not_run` | The loop ended before this step |

A step that did not run holds its status and nothing else. The facts each step records:

| Step | Facts |
|---|---|
| `extraction` | The extraction prompt version and the `text_sha256` of the letter document |
| `rules` | The rules version, "today", the amount floor, the outcome and every reason that fired |
| `scoring` | The feature version, the model version, the score and the threshold |
| `retrieval` | The search question version, the embedding model, the minimum similarity the search used (`min_similarity`), and per result the four citation fields (`source`, `source_document_id`, `chunk_index`, `text_sha256`, see 3.13) and its `similarity` |
| `drafting` | The drafting prompt version, the provider role and the model name; for a checked letter the number of paragraphs with a citation; for a failure its kind (`blocked`, `cut_off`, `not_parseable`, `placeholder_text`, `fact_check`) and, when blocked, the kinds of problem the validator found; since `v5` the number of attempts (`attempts`, 1 or 2; the failure is the last attempt's) |

**A trace never holds document text, an extracted value, the search question or letter
text.** It holds versions, outcomes, numbers and the values that name a chunk. A test
guards this. The letter itself is in `appeal_drafts`; the trace names it by the claim and
the drafting prompt version.

Any change to a step, to what stops the loop or to an end state needs a new `AGENT_VERSION`
(today `v5`).

| Version | Means |
|---|---|
| `v1` | The search had no minimum: the top 5 results whatever their score |
| `v2` | The search drops a result below 0.55, and the trace records the minimum |
| `v3` | Search question `v3` and drafting prompt `v2` (3.15); a draft with text in square brackets is refused, with the new failure kind `placeholder_text` |
| `v4` | Drafting prompt `v3` with its new user message (no claim totals, the reasons in plain words, 3.15); a draft with a wrong dollar amount or "other" as the reason is refused, with the new failure kind `fact_check`. The search question stays `v3` |
| `v5` | When the validator blocks a draft or its answer cannot be parsed, the model is asked once more with the same search result (`MAX_DRAFT_ATTEMPTS` 2); a cut-off answer, placeholder text or a fact-check refusal is not tried again. The trace counts the `attempts`. Prompts and search question unchanged. Used by the batch command and the upload job. Today's version |

**A stored `v1` to `v4` trace still validates** as an `AgentTrace`; a test guards
each. `min_similarity` is optional in the type: it is empty in a step that did not run and
in a stored `v1` trace; `attempts` is empty in a trace before `v5`. `placeholder_text` and
`fact_check` are two more values of the failure kind, and they are stored inside the `trace` JSON, so they needed no migration. For
a `fact_check` failure the trace holds the value alone, not the kinds of wrong fact. A new agent version also means
every claim is taken again, because a claim is skipped only for a trace of the same version
(see "A run over many claims").

### The table

`agent_runs` has one row per claim and agent version. It is a public reference table with
no `account_id`: it holds only runs on the made-up claims of the synthetic sample, like
`appeal_drafts` (3.15). A customer's run will get its own business table with `account_id`.

| Column | Holds |
|---|---|
| `source_claim_id` | The claim, a link to `claim_samples`; removing a claim removes its runs |
| `agent_version` | The agent version; never empty |
| `end_state` | One of the five end states, as the constrained type `agent_end_state` |
| `text_sha256` | The hash of the denial letter document the loop read |
| `trace` | The `AgentTrace` as JSON |
| `created_at`, `updated_at` | As on every table |

`source_claim_id` and `agent_version` are unique together, so a new agent version keeps the
old runs.

Two checks keep the columns and the trace in step: the end state inside `trace` must equal
the `end_state` column, and the agent version inside `trace` must equal the `agent_version`
column. A trace without one of those keys is refused too. The table does not check the rest
of the trace. The only code that writes it is `save_agent_run`, which is given what the
loop returned and is read back with `AgentTrace.model_validate`.

**No foreign key points at `appeal_drafts` or at the evidence tables.** A new load of the
corpus gives every chunk a new row id (3.13), and it must not remove a run.

### A run over many claims

`run_agent` takes the claims the command selected and works through them one at a time.
Each trace is committed on its own, so a run that stopped keeps what it finished.

**Which claims are selected.** The same claims the drafting command selects (3.15): a
denied claim of the chosen split whose denial letter has a current extraction, in claim id
order, so the same `--limit` always means the same claims. A claim without a current
extraction is never given to the loop; the command counts it as left out.

**The claim rows must still fit the label.** The features are built from the claim's stored
label and lines. When a selected claim has no lines, or the label rule applied to its lines
no longer gives its stored label, the whole run is refused before any call
(`ClaimRowsError`, exit code 1). The message gives a count and names no claim.

**Which are skipped.** A claim whose stored trace of this agent version ended as
`needs_review`, `needs_review_no_draft`, `stopped_by_rules` or `below_threshold` is skipped
and costs nothing.

**A `draft_failed` claim is taken again** by the next run and its row is replaced. So a run
takes 0 claims and spends $0 only once no selected claim is left as `draft_failed`.

**An error that says nothing about the claim stops the run.** These pass through
`run_agent_for_claim` with no trace. `run_agent` stores no row for that claim, ends without
a traceback and gives a plain stop reason; the next run takes the claim. They are the same
five cases as in 3.15:

| Case | Why the run stops |
|---|---|
| The monthly budget is reached | Every further call would be refused |
| No provider can answer | The next claim would most likely fail the same way |
| A provider refuses a request (a bad request, a wrong key) | The same |
| The evidence has no stored vector | Nothing can be searched until the embedding run (3.14) |
| The answer to a search question's embedding call cannot be used | A wrong model or vector size would repeat on every claim, each time paid for |

The command's report gives the count per end state, the claims finished before, the claims
left out, the time and the money. It prints no letter text, no document text and no
extracted value.

### Settings

| Key | Required | Meaning |
|---|---|---|
| `AGENT_WIN_THRESHOLD` | no (default 0.30) | The lowest win-model score the loop still drafts for, from 0 to 1 |

A value outside 0 to 1, "not a number" or text fails when the settings are loaded, with a
message that names the key. The command passes `settings.agent_win_threshold` to the loop,
and the loop refuses a value outside 0 to 1 once more with a `ValueError`.

The value 0.30 is a **choice**, not a measurement. The score estimates a made-up label
(3.10). On the train split 0.30 drops the 13% of denied claims with the lowest score and
keeps 96.7% of the claims whose proxy label is true (measured read-only on 2026-10-11).

The loop also uses `RULES_AMOUNT_FLOOR_USD` (3.11), `MODEL_DIR` (3.10) and the `LLM_` keys
(3.8).

### Measured

Measured on 2026-10-11 on the local database, after migration 0013: the test split,
`--limit 50`, `--rules-today 2010-01-01`, amount floor 25, threshold 0.30, agent `v1`,
drafting prompt `v1`, model `gpt-4o-mini`. The split has 759 denied claims; 749 have a
current letter extraction and 10 were left out.

These are the numbers of agent `v1`, **before the minimum similarity**. The later runs are
in 3.17: agent `v2` under "Second measurement", agent `v3` under "Third measurement" (both
on the same first 120 test claims), agent `v4` under "Fourth measurement" (on the first
240) and again under "Fifth measurement" (on the first 360). In short, for agent `v3` on
2026-10-12: 54 `needs_review`, 15 `needs_review_no_draft`, 48 `stopped_by_rules`, 1 `below_threshold` and 2 `draft_failed`
after two runs, for $0.040899; all 54 drafts carry a citation, and 0 drafts were refused
for placeholder text. And for agent `v4` on 2026-10-12, with `--limit 240`: 106
`needs_review`, 24 `needs_review_no_draft`, 105 `stopped_by_rules`, 2 `below_threshold` and
3 `draft_failed` after two runs, for $0.077256; 105 of the 106 drafts carry a citation, and
0 drafts were refused for placeholder text or by the fact check. And for agent `v4` on
2026-10-15, with `--limit 360` (two more runs, $0.035032): 150 `needs_review`, 35
`needs_review_no_draft`, 164 `stopped_by_rules`, 7 `below_threshold` and 4 `draft_failed`
in all for the first 360.

| Run | Claims taken | `needs_review` | `needs_review_no_draft` | `stopped_by_rules` | `below_threshold` | `draft_failed` | Cost | Time |
|---|---|---|---|---|---|---|---|---|
| 1 | 50 | 26 | 5 | 18 | 0 | 1 (not parseable) | $0.013362 | 127 seconds |
| 2 | 1 | 1 | 0 | 0 | 0 | 0 | $0.000467 | 5 seconds |
| 3 | 0 | 0 | 0 | 0 | 0 | 0 | $0 | not recorded |

All three runs exited with 0. Run 2 took the one failed claim again and skipped the 49
others.

| Number | Value |
|---|---|
| Rows in `agent_runs` after the three runs | 50: 27 `needs_review`, 5 `needs_review_no_draft`, 18 `stopped_by_rules` |
| `needs_review` claims with a draft in `appeal_drafts` | 27 of 27 |
| `needs_review` drafts with at least one citation | 10 of 27 |
| Evidence results named in the traces | 135 (27 claims, 5 each) |
| Of them, results whose four values name a chunk in `evidence_chunks` | 135 of 135 |
| Scores of the 27 claims that passed the rules | 0.3749 to 0.8298, all at or above 0.30 |
| Answered by the fallback | 0 |
| Cost of the three runs | $0.013829 (the limit set for the run was $0.05) |
| Cost of one claim that reaches drafting | about $0.0005; a claim stopped earlier costs $0 |
| Month-to-date spend after the runs | $0.745758 of the $3.00 cap |

The rows on drafts and chunks were checked with one-off read-only queries, not a command.

The reasons the rules gave for the 23 claims they did not let through:

| Reason | Claims |
|---|---|
| `deadline_passed` | 18 |
| `amount_zero` | 4 |
| `deadline_not_after_letter_date` | 2 |
| `deadline_missing` | 1 |
| `amount_below_floor` | 1 |

A claim can have two reasons, so the reasons add up to more than 23.

The numbers match what was measured before the code was written (18 stopped, 5 to a person,
0 below the threshold, 27 reach drafting). They hold only while `document_extractions`, the
saved model and the claim selection stay as they were on 2026-10-11.

The share of drafts a reviewer accepts (the 60% target of Stage 4) is **not** measured
here. It is measured by the draft acceptance review (see 3.17).

### Known limits

About scoring:

- An uploaded letter has no claim rows, so its features come from the letter's extracted
  totals (`build_letter_features`, 3.19). The same three features, read two ways, can disagree
  when the extraction is wrong; the model was trained only on features from claim rows.
- The score estimates a made-up label (the proxy of
  [02-data.md 2.2.3](02-data.md#223-labels-rule-v2)). It says nothing about real appeals,
  and so neither does the threshold.
- `below_threshold` is proven by the tests and not by the real run of this section: none of
  the 50 claims scored below 0.30. The later runs saw it: 1 of the first 120 test claims,
  2 of the first 240 and 7 of the first 360 end as `below_threshold` (3.17).

About what a later run looks at:

- A skipped claim is not looked at again when "today", the amount floor, the threshold or
  the drafting prompt version changes. Its stored trace keeps the values of the run that
  wrote it. Looking at every claim again needs a new agent version.
- A trace is not redone when the claim's letter document changes. It can be found as out of
  date, because its `text_sha256` no longer equals the stored letter's, but nothing marks
  it.
- A `draft_failed` claim is tried again on every run, and nothing limits the number of
  tries. A claim whose extracted amount is below zero fails at the rules each time; that
  costs nothing, and it never ends.

About stored drafts:

- 23 drafts in `appeal_drafts` belong to claims the rules do not let through: 18 that end
  as `stopped_by_rules` and 5 as `needs_review_no_draft`. The drafting command of 3.15
  wrote them before the loop existed, and the loop does not remove them. So a row in
  `appeal_drafts` does not mean the loop let the claim through; only a `needs_review` row
  in `agent_runs` does.
- The draft and the trace are stored in two commits. A crash between them leaves a draft
  without a trace. The next run takes the claim again, pays for one more draft and replaces
  the first.
- With agent `v1`, 17 of the 27 drafts of the run measured in this section make no policy
  statement. That is the weak search question of 3.15. With agent `v2` the question text
  is the same and the minimum drops the weak results, so 41 of the 56 drafts get no
  evidence at all.
- The run of agent `v2` wrote over the drafts of agent `v1`. Both used drafting prompt
  `v1` and `appeal_drafts` has one row per claim and prompt version, so the 55 drafts of
  the first review are no longer in the table. The `v1` traces stay in `agent_runs`, but a
  `v1` trace that ended as `needs_review` now describes a draft that was replaced. Keeping
  both drafts of a claim would need a migration.
- The run of agent `v3` did **not** write over anything: it uses drafting prompt `v2`, so
  its 54 drafts are stored beside the 56 of agent `v2`. Checked after the run:
  `agent_runs` holds 120 traces each of `v1`, `v2` and `v3`, and `appeal_drafts` holds 54
  drafts of prompt `v2` and 79 of prompt `v1`.
- With agent `v3`, 2 of the 120 claims are still `draft_failed`: the validator blocked
  their draft in both runs, and no third run was made. So "a run takes 0 claims and spends
  $0 once no claim is left as `draft_failed`" was **not** checked on the real data for
  `v3`. Tests prove it, and the real runs of `v1` and `v2` showed it.
- The run of agent `v4` wrote over nothing either: it uses drafting prompt `v3`, so its 106
  drafts are stored beside the earlier ones. Checked after the run: `agent_runs` holds 120
  traces each of `v1`, `v2` and `v3` and 240 of `v4`; `appeal_drafts` holds 106 drafts of
  prompt `v3`, 54 of prompt `v2` and 79 of prompt `v1`.
- With agent `v4`, 3 of the 240 claims are still `draft_failed`: the validator blocked
  their draft in both runs, and no third run was made. So the run that takes 0 claims for
  $0 was **not** made for `v4` either. The next run would take those 3 again and pay for
  them.
- The failure kind `fact_check` is proven by the tests and not by a real run: the fact
  check refused 0 drafts of agent `v4`.

About the run that was measured:

- Why the one answer of run 1 could not be parsed was not looked into. The claim got a
  draft on the next try.
- Every answer came from the primary model. Whether the fallback model can fill the draft
  shape was not tried.
- When the month has no recorded call at all, the report prints the spend as `$0`, without
  the six decimal places it has otherwise.

About the budget:

- Two processes that call a model at the same moment can both pass the budget check (3.8).

## 3.17 Draft acceptance review

The draft acceptance review measures the Stage 4 done condition in
[05-build-plan.md](05-build-plan.md): a reviewer accepts at least 60% of drafts with only
light edits. A seeded sample of the drafts the agent loop stored (3.16) is written to a file,
a reviewer gives every draft one verdict from a written rubric, and one command works out
the share.

Three things hold for the command:

- **It only reads the database.** It makes no model call, spends no money and stores
  nothing in a table. Tests prove it.
- **Nothing in the code scores a draft.** The verdicts come from a file the reviewer fills
  in.
- **The terminal shows counts and shares only.** Letter text and document text go into one
  local file and are never printed.

The share has been measured five times, twice on 2026-10-11, twice on 2026-10-12 and once
on 2026-10-15:

| Measurement | Drafts of | Accepted | Scored by |
|---|---|---|---|
| First | agent `v1`, search question `v1`, drafting prompt `v1` | **0 of 30 (0.00%)** | a model |
| Second | agent `v2`, search question `v2` (the minimum similarity of 3.15), drafting prompt `v1` | **0 of 30 (0.00%)** | a model |
| Third | agent `v3`, search question `v3` (new phrases, no codes), drafting prompt `v2`, the four manual chapters in the evidence | **17 of 30 (56.67%)** | a model, with a new and easier reading of the rubric |
| Fourth | agent `v4`, search question `v3`, drafting prompt `v3` (no claim totals and no category values in the message, three citation rules), the fact check; a new sample from twice as many claims | **20 of 30 (66.67%)** | a model, with the reading of the third |
| Fifth | agent `v4`, search question `v3`, drafting prompt `v3`, unchanged; a new sample only from claims no earlier review had read, drawn from 360 claims | **23 of 30 (76.67%)** | a model, with the reading of the third |

Every verdict was given **by a model, not by a person** (see "Who scored" below).

**The fifth share meets the 60% target, and it decides the release.** Before any of its
drafts was read, the rule was written down: Stage 4 is released as `v0.5.0` when the fifth
sample alone is at least 60% (18 of 30). It is 23 of 30. The model marked 10 of its
verdicts as borderline, which move the share from 50.00% to 83.33%; that range was to be
recorded, not to change the decision (see "Fifth measurement").

**The fourth number is not firm.** 7 of its 30 verdicts are marked as borderline by the
model that gave them: the share is 53.33% if the 4 borderline accepts are rejected, and
76.67% if the 3 borderline rejects are accepted (see "Fourth measurement"). The project
owner did not release on it and had the fifth measurement made first.

**The third number is not firm either.** It rests on single judgement calls by the model
that scored. Read in three other ways, the same 30 drafts give 23.33%, 33.33% or up to 70%
(see "Third measurement").

### Where the code is

| File | Holds |
|---|---|
| `src/evaluation/review_rubric.py` | The rubric: `Verdict`, `RejectReason`, their meanings, `ACCEPTED_VERDICTS`, `ACCEPTANCE_TARGET` (0.60) and `LIGHT_EDIT_SENTENCES` (3) |
| `src/evaluation/review_sample.py` | `select_review_sample(claim_ids, *, size, seed)`, the seeded draw, and the defaults `REVIEW_SAMPLE_SIZE` (30) and `REVIEW_SAMPLE_SEED` (42) |
| `src/evaluation/review_store.py` | `select_reviewable_claims` (the claims whose draft can be reviewed), `read_review_drafts`, `read_review_items` (what the reviewer sees), `draft_fingerprint` and `ReviewDataError` |
| `src/evaluation/review_export.py` | `build_review_text` (the file to read) and `build_scores_csv` (the score file to fill in) |
| `src/evaluation/review_score.py` | `read_review_scores`, `check_review_scores`, `count_without_verdict`, `build_review_report`, the printed `ReviewReport` and `ScoreFileError` |
| `pipelines/review_drafts.py` | The command with its two steps, `export` (with `--exclude-scored`, which leaves out the claims of earlier score files) and `score` ([02-data.md 2.9](02-data.md#29-running-the-whole-pipeline)) |

The sampling function and the functions that build the two files and the report are pure.
Only `review_store.py` reads the database and only the command writes files. There is no
table and no migration.

### The rubric

The reviewer answers one question per draft: would you send this letter? The wording below
is the wording of `review_rubric.py`, which is also printed at the top of the file the
reviewer reads.

| Verdict | Means |
|---|---|
| `accept` | you would send the draft as it is. |
| `accept_light_edits` | you would send it after changing the wording of at most 3 sentences. No fact is corrected and no policy argument is added or removed. |
| `reject` | anything else. A fact that differs from the denial letter (name, claim number, date, amount, service), a cited quote that does not support the sentence it sits under, no real argument for overturning the denial, or a rewrite of more than 3 sentences. |

A `reject` needs one reason. The other two verdicts must not have one.

| Reject reason | Means |
|---|---|
| `wrong_fact` | a fact differs from the denial letter. |
| `no_policy_argument` | the draft gives no real argument for overturning the denial. |
| `citation_does_not_fit` | a cited quote does not support the sentence it sits under. |
| `poor_wording` | more than 3 sentences need rewording. |
| `other` | none of the above; say why in the note. |

**The acceptance share** is the drafts with `accept` or `accept_light_edits`, divided by
all scored drafts. A draft with no citation is judged by the same rubric.

### The method

**Which drafts can be sampled.** A claim whose trace in `agent_runs` ended as
`needs_review` for the current agent version (`AGENT_VERSION`, today `v4`), that has a draft
in `appeal_drafts` of the current drafting prompt version (today `v3`; the first two
measurements read agent `v1` and `v2` with prompt `v1`, the third agent `v3` with prompt
`v2`), and whose denial
letter document stored now is the one the draft was built from. A claim with
another end state or with no trace is never sampled, so the 23 older drafts on claims the
rules now stop (3.16) are left out. A draft whose letter document changed is left out too
and counted.

**The draw.** `select_review_sample` sorts the claim ids and draws with
`random.Random(seed)`. The same ids and the same seed always give the same sample, whatever
order the ids arrive in. A size above the number of ids, a size below 1 and a repeated id
are refused. Every measured review used 30 drafts. The first three used seed 42, the
fourth seed 43 and the fifth seed 44.

**Leaving out claims that were read before.** `export --exclude-scored <score csv>` takes
out of the pool every claim named in that score file, before the draw. The option can be
given once per file. Each file is read with `read_review_scores`, the reader `score` uses,
so a file whose verdicts are still empty can be named too. A missing file or one that
cannot be used stops the command with exit 1 before the database is opened, and no file is
written. The printed headline counts the pool after leaving out, and one more line says how
many were left out. Without the option the pool and the sample are the same as before. The
fifth measurement named the four model score files of the earlier reviews.

**What the reviewer sees.** Per draft: every paragraph of the draft with its quotes, the
text of the denial letter document, and every cited chunk in full with its determination
title and section number.

**What the reviewer does not see.** The win score, the rules verdict and the
appeal-success proxy. The code that builds the file never reads them, and a test guards
it.

**Where the scores live.** `export` writes two files into the folder given as an argument:
`review_drafts.md` to read and `review_scores.csv` to fill in. The folder must be one that
is never committed, such as `data/review/`, because the file to read holds document text.
An existing score file is never overwritten.

**The fingerprint.** Every score row carries the SHA-256 of the stored letter, written as
JSON with sorted keys. When a claim is drafted again, its fingerprint changes and `score`
refuses the row. So do not run the agent loop between `export` and `score`.

**What `score` checks before it gives a share.** The verdict is one of the three; a
`reject` has a reason and the other two have none; the reason is one of the five; no claim
is scored twice; the number of rows equals `--size`; every row belongs to a draft that can
be reviewed. Every problem about a row names its line in the file (for a row whose note
runs over several lines, the last of them), and no problem quotes a value from the file.
A file with an empty verdict gives no share at all, only the number of drafts still open.

**The 60% is a gate.** `score` exits with 0 at 60% or more and with 1 below it. A share of
exactly 60% passes. A printed share is never rounded up to a target it misses.

### Measured

This is the first measurement, on the drafts of agent `v1`. The second is in the next
sub-section.

Measured on 2026-10-11 on the local database, at migration 0013.

**Step 1, more drafts.** The agent loop ran once more, on the test split with
`--limit 120` and `--rules-today 2010-01-01` (amount floor 25, threshold 0.30, agent `v1`,
drafting prompt `v1`, model `gpt-4o-mini`). 50 of the 120 claims were finished before
(3.16).

| Run | Claims taken | `needs_review` | `needs_review_no_draft` | `stopped_by_rules` | `below_threshold` | `draft_failed` | Cost | Time |
|---|---|---|---|---|---|---|---|---|
| 1 | 70 | 26 | 10 | 30 | 1 | 3 (not parseable) | $0.014247 | 141 seconds |
| 2 | 3 | 2 | 0 | 0 | 0 | 1 (blocked) | $0.001546 | 14 seconds |

Both runs exited with 0. The blocked claim was not tried a third time.

| Number | Value |
|---|---|
| Rows in `agent_runs` after the two runs | 120: 55 `needs_review`, 15 `needs_review_no_draft`, 48 `stopped_by_rules`, 1 `below_threshold`, 1 `draft_failed` |
| `needs_review` drafts with at least one citation | 27 of 55 |
| Cost of the two runs | $0.015793 (the limit set for the run was $0.05) |
| Month-to-date spend after the runs | $0.761551 of the $3.00 cap |

**Step 2, the sample.** `export` drew 30 of the 55 drafts that can be reviewed, with seed
42. 15 of the 30 carry at least one citation. No draft was left out for a changed letter.

**Step 3, the verdicts.**

| Number | Value |
|---|---|
| Drafts scored | 30 |
| `accept` | 0 |
| `accept_light_edits` | 0 |
| `reject` | 30 |
| **Acceptance share** | **0 of 30 (0.00%), below the 60% target** |
| Drafts with a citation | 0 of 15 accepted |
| Drafts without a citation | 0 of 15 accepted |

| Reject reason | Drafts |
|---|---|
| `citation_does_not_fit` | 14 |
| `no_policy_argument` | 13 |
| `wrong_fact` | 2 |
| `poor_wording` | 0 |
| `other` | 1 |

The command exited with 1. **A model gave these 30 verdicts, not a person.**

### Second measurement (search question `v2`)

The first measurement rejected 14 of 30 drafts because the cited text did not fit. So the
search got a minimum similarity of 0.55 (3.15), and the share was measured again with the
same rubric, the same command, the same sample size and the same seed. Measured on
2026-10-11 on the local database, at migration 0013.

**Step 1, the loop again.** Agent `v2` ran on the same 120 test claims (`--limit 120`,
`--rules-today 2010-01-01`, amount floor 25, threshold 0.30, drafting prompt `v1`, model
`gpt-4o-mini`). A new agent version takes every claim again.

| Run | Claims taken | `needs_review` | `needs_review_no_draft` | `stopped_by_rules` | `below_threshold` | `draft_failed` | Cost | Time |
|---|---|---|---|---|---|---|---|---|
| 1 | 120 | 56 | 15 | 48 | 1 | 0 | $0.019025 | 230 seconds |
| 2 | 0 | 0 | 0 | 0 | 0 | 0 | $0.000000 | 0 seconds |

Both runs exited with 0. Run 2 found all 120 claims finished and made no call.

| Number | Value | First measurement |
|---|---|---|
| `needs_review` drafts | 56 | 55 |
| Of them, with at least one citation | 13 | 27 |
| Of them, drafted with no evidence (the search returned no match) | 41 | 0 |
| Evidence results named in the `v2` traces | 37, similarity 0.5501 to 0.6634 | 5 per claim, no minimum |
| Citations in the drafts | 14, in 13 drafts, to 5 distinct determinations | 28, to 5 distinct determinations |
| Of them, with a similarity in the claim's trace of at least 0.55 | 14 of 14 (0.5504 to 0.6634) | 10 of 28 |
| Of them, whose four values name a chunk in `evidence_chunks` | 14 of 14 | not recorded |
| Cost of the two runs | $0.019025 (the limit set for the run was $0.05) | $0.015793 |
| Month-to-date spend after the runs | $0.780622 of the $3.00 cap | $0.761551 |

The rows on citations and results were checked with one-off read-only queries, not a
command.

The month-to-date spend rose by $0.019071 between the two measurements: $0.019025 for the
two runs and $0.000046 for 46 query embeddings made while the minimum of 0.55 was chosen
(3.15).

After the runs `agent_runs` holds 120 traces of `v1` and 120 of `v2`. `appeal_drafts` holds
the 56 drafts of search question `v2` and the 23 older drafts of 3.16.

**Step 2, the sample.** `export` wrote into a new folder and drew 30 of the 56 drafts that
can be reviewed, with seed 42. 6 of the 30 carry at least one citation. No draft was left
out for a changed letter. 29 of the 30 claims are also in the first sample; every one of
them has a new draft.

**Step 3, the verdicts.**

| Number | Second measurement | First measurement |
|---|---|---|
| Drafts scored | 30 | 30 |
| `accept` | 0 | 0 |
| `accept_light_edits` | 0 | 0 |
| `reject` | 30 | 30 |
| **Acceptance share** | **0 of 30 (0.00%), below the 60% target** | **0 of 30 (0.00%)** |
| Drafts with a citation | 0 of 6 accepted | 0 of 15 accepted |
| Drafts without a citation | 0 of 24 accepted | 0 of 15 accepted |

| Reject reason | Second measurement | First measurement |
|---|---|---|
| `no_policy_argument` | 21 | 13 |
| `citation_does_not_fit` | 6 | 14 |
| `wrong_fact` | 3 | 2 |
| `poor_wording` | 0 | 0 |
| `other` | 0 | 1 |

The command exited with 1. **A model gave these 30 verdicts, not a person**, and it was the
same model that had given the first 30 and knew their result.

### Third measurement (search question `v3`, drafting prompt `v2`)

The second measurement rejected 21 of 30 drafts for giving no policy argument. Two things
changed before the third. Four CMS manual chapters were loaded as a second evidence source
(3.13), so the corpus now holds general rules: exclusions, "not reasonable and necessary",
appeal rights. And search question `v3`, drafting prompt `v2` and the placeholder check
were built (3.15). The rubric text, the command, the sample size and the seed are the same.
**The reading of the rubric is not the same**; see below. Measured on 2026-10-12 on the
local database, at migration 0014.

**The reading of the rubric, fixed before any draft.** The rubric says a draft needs a
"real argument for overturning the denial". On 2026-10-12, before any draft of prompt `v2`
existed, it was written down what that means for each denial reason, so that the reading
could not bend to the result:

| Denial reason | A draft is accepted only when |
|---|---|
| `noncovered`, `medical_necessity`, `coordination_of_benefits` | a policy paragraph ties a cited rule to that denial reason. A citation of the right to appeal alone is `no_policy_argument` |
| `other` (the denial says only "could not be paid as submitted") | its facts are right, it says the denial names no specific reason, it cites the right to a redetermination, and it asks for the reason and a second look |

**The second row is new and makes the target easier to reach.** Reviews 1 and 2 would have
rejected such an `other` letter as `no_policy_argument`. Every note in the model's score
file names the reading.

**Step 1, the loop again.** Agent `v3` ran on the same 120 test claims (`--limit 120`,
`--rules-today 2010-01-01`, amount floor 25, threshold 0.30, drafting prompt `v2`, model
`gpt-4o-mini`).

| Run | Claims taken | `needs_review` | `needs_review_no_draft` | `stopped_by_rules` | `below_threshold` | `draft_failed` | Cost | Time |
|---|---|---|---|---|---|---|---|---|
| 1 | 120 | 47 | 15 | 48 | 1 | 9 (8 blocked, 1 cut off) | $0.035382 | 314 seconds |
| 2 | 9 | 7 | 0 | 0 | 0 | 2 (blocked) | $0.005517 | 53 seconds |

Both runs exited with 0. Run 2 took the 9 failed claims again. Its 2 blocked drafts had the
problems `quote_not_in_evidence` and `policy_without_citation`. **No third run was made**, so
2 claims stay `draft_failed` and the run that takes 0 claims for $0 was not made (3.16).

| Number | Third measurement | Second measurement |
|---|---|---|
| Rows in `agent_runs` for the agent version | 120: 54 `needs_review`, 15 `needs_review_no_draft`, 48 `stopped_by_rules`, 1 `below_threshold`, 2 `draft_failed` | 120: 56, 15, 48, 1, 0 |
| `needs_review` drafts | 54 | 56 |
| Of them, with at least one citation | 54 | 13 |
| Of them, drafted with no evidence | 0 | 41 |
| Drafts refused for placeholder text | 0 | no such check |
| Evidence results named in the traces | 280 (56 claims, 5 each), lowest similarity 0.5788 | 37, similarity 0.5501 to 0.6634 |
| Citations in the drafts | 60: 48 drafts with one policy paragraph, 6 with two | 14, in 13 drafts |
| Of them, from `cms_manual` | 60 | none; the source was not loaded |
| Of them, from `cms_ncd` | 0 | 14 |
| Of them, in the claim's trace with a similarity of at least 0.55 | 60 of 60 (lowest 0.6295) | 14 of 14 |
| Of them, whose four values name a chunk in `evidence_chunks` | 60 of 60 | 14 of 14 |
| Answered by the fallback | 0 | 0 |
| Cost of the two runs | $0.040899 (the limit set for the run was $0.05) | $0.019025 |
| Month-to-date spend after the runs | $0.827819 of the $3.00 cap | $0.780622 |

The rows on citations and results were checked with one-off read-only queries, not a
command. Before the run the month-to-date spend was $0.786920.

The 60 citations name 8 manual sections:

| Section | Title | Citations |
|---|---|---|
| `100-02-16-40` | No Legal Obligation to Pay for or Provide Services | 25 |
| `100-04-29-310` | Redetermination - The First Level of Appeal | 23 |
| `100-04-29-220` | Steps in the Appeals Process: Overview | 3 |
| `100-02-16-40.3` | Medicare Patient Has Other Health Coverage | 3 |
| `100-02-16-10` | General Exclusions from Coverage | 2 |
| `100-02-16-20` | Services Not Reasonable and Necessary | 2 |
| `100-04-29-310.1` | Filing a Request for Redetermination | 1 |
| `100-04-30-20.1` | LOL Coverage Denials | 1 |

After the runs `agent_runs` holds 120 traces each of `v1`, `v2` and `v3`. `appeal_drafts`
holds the 54 new drafts of prompt `v2` beside the 79 drafts of prompt `v1` (56 of search
question `v2` and the 23 older ones of 3.16). Nothing earlier was written over.

**Step 2, the sample.** `export` wrote into a new folder and drew 30 of the 54 drafts that
can be reviewed, with seed 42. All 30 carry at least one citation. No draft was left out
for a changed letter.

**Step 3, the verdicts.**

| Number | Third measurement | Second | First |
|---|---|---|---|
| Drafts scored | 30 | 30 | 30 |
| `accept` | 2 | 0 | 0 |
| `accept_light_edits` | 15 | 0 | 0 |
| `reject` | 13 | 30 | 30 |
| **Acceptance share** | **17 of 30 (56.67%), below the 60% target** | **0 of 30 (0.00%)** | **0 of 30 (0.00%)** |
| Drafts with a citation | 17 of 30 accepted | 0 of 6 | 0 of 15 |
| Drafts without a citation | none in the sample | 0 of 24 | 0 of 15 |

| Reject reason | Third measurement | Second | First |
|---|---|---|---|
| `wrong_fact` | 8 | 3 | 2 |
| `citation_does_not_fit` | 4 | 6 | 14 |
| `no_policy_argument` | 1 | 21 | 13 |
| `poor_wording` | 0 | 0 | 0 |
| `other` | 0 | 0 | 1 |

By the reason the denial letter gives, as the model read it from the 30 letters:

| Denial reason of the letter | Accepted |
|---|---|
| `noncovered` | 10 of 15 |
| `other` | 7 of 13 |
| `medical_necessity` | 0 of 1 |
| `coordination_of_benefits` | 0 of 1 |

The command exited with 1. **A model gave these 30 verdicts, not a person.** It was the same
model that gave the first 60, it knew both earlier results, and it wrote both the reading
above and the `other` rule of prompt `v2`.

**How firm the number is.** 17 of 30 is 1 draft short of the target, and it moves a long
way with the reading. The same 30 drafts, read in three other ways:

| Reading | Accepted | Why |
|---|---|---|
| The one recorded | **17 of 30 (56.67%)** | |
| The one-quote `noncovered` argument does not count | 7 of 30 (23.33%) | All 10 accepted `noncovered` letters rest on one sentence of one passage, "an analysis of each payment exclusion must be made before a determination can be made about whether Medicare can pay", used to ask which exclusion was applied. The passage is a rule about when Medicare may **not** pay. A stricter reviewer would call that no argument |
| The reading of reviews 1 and 2 (no `other` letter is accepted) | 10 of 30 (33.33%) | The 7 accepted `other` letters cite only the right to a second look |
| A lenient reviewer | up to 21 of 30 (70.00%) | Two rejected drafts have one sentence the quote does not support, and two have one wrong word (the category word `other` given as the reason). Fixing a sentence instead of rejecting accepts all four |

The three other figures are the model's own count from its notes, not measurements. None
of them is a second review.

### Fourth measurement (drafting prompt `v3`, the fact check)

The third measurement rejected 8 of 30 drafts for a wrong fact and 4 for a citation that
does not fit. Three things were built before the fourth (3.15): the user message without
the claim totals and with the reasons in plain words, the fact check, and drafting prompt
`v3` with three rules for the citation faults. The search question, the evidence, the
rubric text and the command are the same. **The sample is not the same**: it is drawn with
another seed from twice as many claims. Measured on 2026-10-12 on the local database, at
migration 0014.

**The reading of the rubric is the one of the third measurement**, unchanged (the table
under "Third measurement"). It was written at the top of the model's score file before any
draft of the new sample was read, and every note names it.

**Step 1, the loop on 240 claims.** Agent `v4` ran on the first 240 test claims
(`--limit 240`, `--rules-today 2010-01-01`, amount floor 25, threshold 0.30, drafting
prompt `v3`, model `gpt-4o-mini`): the 120 claims of the first three measurements and the
next 120 in claim id order. A new agent version takes every claim again.

| Run | Claims taken | `needs_review` | `needs_review_no_draft` | `stopped_by_rules` | `below_threshold` | `draft_failed` | Cost | Time |
|---|---|---|---|---|---|---|---|---|
| 1 | 240 | 92 | 24 | 105 | 2 | 17 (all blocked) | $0.066647 | 565 seconds |
| 2 | 17 | 14 | 0 | 0 | 0 | 3 (blocked) | $0.010609 | 95 seconds |

Both runs exited with 0. Run 2 took the 17 failed claims again. Its 3 blocked drafts had
the problems `quote_not_in_evidence` (2) and `policy_without_citation` (1). **No third run
was made**, so 3 claims stay `draft_failed` and the run that takes 0 claims for $0 was not
made (3.16). Which problems the 17 blocked drafts of run 1 had was not recorded.

| Number | Fourth measurement | Third measurement |
|---|---|---|
| Claims the loop took | 240 | 120 |
| Rows in `agent_runs` for the agent version | 240: 106 `needs_review`, 24 `needs_review_no_draft`, 105 `stopped_by_rules`, 2 `below_threshold`, 3 `draft_failed` | 120: 54, 15, 48, 1, 2 |
| `needs_review` drafts | 106: 54 among the first 120 claims, 52 among the new 120 | 54 |
| Of them, with at least one citation | 105 | 54 |
| Of them, drafted with no evidence | 1 (a `benefits_exhausted` claim; its phrase reaches no passage, 3.15) | 0 |
| Drafts refused for placeholder text | 0 | 0 |
| Drafts refused by the fact check | 0 | no such check |
| Evidence results named in the traces | 540 (108 claims, 5 each), lowest similarity 0.5788 | 280 (56 claims, 5 each), lowest 0.5788 |
| Citations in the drafts | 135: 75 drafts with one policy paragraph, 30 with two | 60: 48 with one, 6 with two |
| Of them, from `cms_manual` | 135 | 60 |
| Of them, from `cms_ncd` | 0 | 0 |
| Of them, in the claim's trace with a similarity of at least 0.55 | 135 of 135 (lowest 0.6116) | 60 of 60 (lowest 0.6295) |
| Of them, whose four values name a chunk in `evidence_chunks` | 135 of 135 | 60 of 60 |
| Answered by the fallback | 0 | 0 |
| Cost of the two runs | $0.077256 (the limit set for the run was $0.10) | $0.040899 |
| Month-to-date spend after the runs | $0.905075 of the $3.00 cap | $0.827819 |

The rows on citations and results were checked with one-off read-only queries, not a
command. Before the run the month-to-date spend was $0.827819.

The 109 claims that reached the search (the 106 drafted and the 3 still `draft_failed`), by
the denial reason their letter extraction holds: `noncovered` 53, `other` 40,
`medical_necessity` 10, `coordination_of_benefits` 5, `benefits_exhausted` 1.

The 135 citations name 9 manual sections:

| Section | Title | Citations |
|---|---|---|
| `100-02-16-40` | No Legal Obligation to Pay for or Provide Services | 56 |
| `100-04-29-310` | Redetermination - The First Level of Appeal | 39 |
| `100-04-29-220` | Steps in the Appeals Process: Overview | 25 |
| `100-02-16-40.3` | Medicare Patient Has Other Health Coverage | 5 |
| `100-02-16-20` | Services Not Reasonable and Necessary | 5 |
| `100-04-30-20.1` | LOL Coverage Denials | 2 |
| `100-02-16-10` | General Exclusions from Coverage | 1 |
| `100-04-30-100.2` | Conditions for Indemnification | 1 |
| `100-04-30-20.2` | Denials When the LOL Provision Does Not Apply | 1 |

After the runs `agent_runs` holds 120 traces each of `v1`, `v2` and `v3` and 240 of `v4`.
`appeal_drafts` holds the 106 new drafts of prompt `v3` beside the 54 of prompt `v2` and
the 79 of prompt `v1`. Nothing earlier was written over.

**Step 2, the sample.** `export` wrote into a new folder and drew 30 of the 106 drafts that
can be reviewed, with **seed 43**. All 30 carry at least one citation. No draft was left
out for a changed letter. 13 of the 30 claims are among the first 120 test claims and 17
are among the new 120. 6 of the 30 claims are also in the sample of the third measurement;
each has a new draft.

The seed and the number of claims were fixed when the work was planned, before any draft
of prompt `v3` existed. The reason: the same 120 claims and seed 42 had been read three
times, and each prompt was written from their faults, so a fourth review of them would not
be a fair test.

**Step 3, the verdicts.**

| Number | Fourth measurement | Third | Second | First |
|---|---|---|---|---|
| Drafts scored | 30 | 30 | 30 | 30 |
| `accept` | 5 | 2 | 0 | 0 |
| `accept_light_edits` | 15 | 15 | 0 | 0 |
| `reject` | 10 | 13 | 30 | 30 |
| **Acceptance share** | **20 of 30 (66.67%), meets the 60% target as scored** | **17 of 30 (56.67%)** | **0 of 30 (0.00%)** | **0 of 30 (0.00%)** |
| Drafts with a citation | 20 of 30 accepted | 17 of 30 | 0 of 6 | 0 of 15 |
| Drafts without a citation | none in the sample | none in the sample | 0 of 24 | 0 of 15 |

| Reject reason | Fourth measurement | Third | Second | First |
|---|---|---|---|---|
| `citation_does_not_fit` | 7 | 4 | 6 | 14 |
| `wrong_fact` | 2 | 8 | 3 | 2 |
| `no_policy_argument` | 1 | 1 | 21 | 13 |
| `poor_wording` | 0 | 0 | 0 | 0 |
| `other` | 0 | 0 | 0 | 1 |

The share for the two halves of the sample:

| Sampled claims | Accepted |
|---|---|
| Among the first 120 test claims (the claims the earlier reviews were drawn from) | 10 of 13 (76.92%) |
| Among the new 120 | 10 of 17 (58.82%) |

By the reason the denial letter gives, as the model read it from the 30 letters:

| Denial reason of the letter | Accepted | Third measurement |
|---|---|---|
| `noncovered` | 11 of 17 | 10 of 15 |
| `other` | 8 of 8 | 7 of 13 |
| `medical_necessity` | 0 of 3 | 0 of 1 |
| `coordination_of_benefits` | 1 of 2 | 0 of 1 |

The command exited with 0. **A model gave these 30 verdicts, not a person.** It was the same
model that gave the first 90, it knew all three earlier results and that the stage is
released at 60%, and it wrote the three new prompt rules and the fact check.

**How firm the number is.** The model marked 7 of its 30 verdicts as borderline in its
notes: 4 that it accepted and 3 that it rejected. 20 of 30 is 2 drafts above the target, so
those 7 decide whether the target is met:

| Reading | Accepted | Why |
|---|---|---|
| The one recorded | **20 of 30 (66.67%)** | |
| The 4 borderline accepts are rejected | 16 of 30 (53.33%), below the target | Each is at the limit of a light edit, or has a sentence that goes past its quote |
| The 3 borderline rejects are accepted | 23 of 30 (76.67%) | Each was rejected for a paragraph its quote does not support; rewording that paragraph instead of rejecting accepts the draft |
| The reading of reviews 1 and 2 (no `other` letter is accepted) | 12 of 30 (40.00%) | The 8 accepted `other` letters cite only the right to a second look |
| The one-quote `noncovered` argument does not count | 9 of 30 (30.00%) | All 11 accepted `noncovered` letters rest on the same sentence of the same passage as in the third measurement, used to ask which exclusion was applied |

The first two other figures are the model's own count from its notes. The last two are
worked out from the counts above. None of them is a second review.

### Fifth measurement (claims no review has read)

The fourth share met the target as scored, but it was not firm, and on the 17 claims no
earlier review had read it was 10 of 17 (58.82%). So the project owner had the share
measured once more, **only on drafts of claims no review and no prompt rule had read**,
before deciding on the release. Nothing that writes or checks a draft changed: agent `v4`,
search question `v3`, drafting prompt `v3`, the fact check, the evidence and the rubric
text are those of the fourth measurement. What is new is the `--exclude-scored` option of
`export` (see "The method"). Measured on 2026-10-15 on the local database, at migration
0014.

**Written down before any draft was read**, at the top of the model's notes in the review
folder:

- **The reading of the rubric** is the one of the third and fourth measurements, unchanged
  (the table under "Third measurement").
- **The decision rule:** Stage 4 is released as `v0.5.0` when the fifth sample alone is at
  least 60%, 18 of 30 or more, and `score` exits with 0. Below that, no release, and the
  next piece of work is chosen from the reject reasons.
- **The borderline verdicts** are marked in the notes and the lowest and highest share they
  give are recorded, but they do not change the decision.

**Step 1, the loop on 360 claims.** The loop ran with `--limit 360` (`--rules-today
2010-01-01`, amount floor 25, threshold 0.30, drafting prompt `v3`, model `gpt-4o-mini`).
The agent version stayed `v4`, so the 240 claims it had finished were skipped: it took the
next 120 test claims in claim id order (candidates 241 to 360) and the 3 claims left as
`draft_failed` by the fourth measurement.

| Run | Claims taken | `needs_review` | `needs_review_no_draft` | `stopped_by_rules` | `below_threshold` | `draft_failed` | Cost |
|---|---|---|---|---|---|---|---|
| 1 | 123 | 40 | 11 | 59 | 5 | 8 (blocked) | $0.029160 |
| 2 | 8 | 4 | 0 | 0 | 0 | 4 (3 blocked, 1 cut off) | $0.005872 |

Both runs exited with 0. The time of the runs was not recorded. **No third run was made**,
so 4 claims stay `draft_failed`.

| Number | Fifth measurement | Fourth measurement |
|---|---|---|
| Claims the loop has finished for agent `v4` | 360 | 240 |
| Rows in `agent_runs` for agent `v4` | 360: 150 `needs_review`, 35 `needs_review_no_draft`, 164 `stopped_by_rules`, 7 `below_threshold`, 4 `draft_failed` | 240: 106, 24, 105, 2, 3 |
| New `needs_review` drafts | 44 | 106 |
| Of them, with at least one citation | 44 | 105 |
| Citations in the new drafts | 52: 36 drafts with one policy paragraph, 8 with two | 135 |
| Of them, from `cms_manual` | 52 | 135 |
| Of them, whose four values name a chunk in `evidence_chunks`, with the quote in its text | 52 of 52 | 135 of 135 name a chunk (whether the quote is in it was not recorded) |
| Cost of the two runs | $0.035032 (the limit set for the run was $0.10) | $0.077256 |
| Month-to-date spend after the runs | $0.940107 of the $3.00 cap | $0.905075 |

The rows on citations were checked with one-off read-only queries, not a command. Of
candidates 241 to 360, 45 reached drafting: `noncovered` 20, `other` 18,
`medical_necessity` 5, `coordination_of_benefits` 2 (measured read-only before the run, with
no model call).

The 52 citations name 7 manual sections:

| Section | Title | Citations |
|---|---|---|
| `100-02-16-40` | No Legal Obligation to Pay for or Provide Services | 19 |
| `100-04-29-310` | Redetermination - The First Level of Appeal | 19 |
| `100-04-29-220` | Steps in the Appeals Process: Overview | 8 |
| `100-02-16-20` | Services Not Reasonable and Necessary | 2 |
| `100-02-16-40.3` | Medicare Patient Has Other Health Coverage | 2 |
| `100-04-30-100.2` | Conditions for Indemnification | 1 |
| `100-04-30-20.1` | LOL Coverage Denials | 1 |

After the runs `appeal_drafts` holds 150 drafts of prompt `v3`. Nothing earlier was
written over.

**Step 2, the sample.** `export` wrote into a new folder with **seed 44** and named the four
model score files of the earlier reviews with `--exclude-scored`. Of the 150 drafts that can
be reviewed, the 60 claims those files name were left out, so the pool was 90. 29 of the 30
sampled drafts carry at least one citation. No draft was left out for a changed letter.

| Sampled claims | Drafts in the sample |
|---|---|
| Candidates 1 to 120 (drafted for agent `v4`, never in a sample) | 6 |
| Candidates 121 to 240 (drafted in the fourth measurement's run, never in a sample) | 12 |
| Candidates 241 to 360 (new in this run) | 12 |

**Step 3, the verdicts.**

| Number | Fifth | Fourth | Third | Second | First |
|---|---|---|---|---|---|
| Drafts scored | 30 | 30 | 30 | 30 | 30 |
| `accept` | 0 | 5 | 2 | 0 | 0 |
| `accept_light_edits` | 23 | 15 | 15 | 0 | 0 |
| `reject` | 7 | 10 | 13 | 30 | 30 |
| **Acceptance share** | **23 of 30 (76.67%), meets the 60% target** | **20 of 30 (66.67%)** | **17 of 30 (56.67%)** | **0 of 30 (0.00%)** | **0 of 30 (0.00%)** |
| Drafts with a citation | 23 of 29 accepted | 20 of 30 | 17 of 30 | 0 of 6 | 0 of 15 |
| Drafts without a citation | 0 of 1 accepted | none in the sample | none in the sample | 0 of 24 | 0 of 15 |

| Reject reason | Fifth | Fourth | Third | Second | First |
|---|---|---|---|---|---|
| `wrong_fact` | 3 | 2 | 8 | 3 | 2 |
| `no_policy_argument` | 3 | 1 | 1 | 21 | 13 |
| `citation_does_not_fit` | 1 | 7 | 4 | 6 | 14 |
| `poor_wording` | 0 | 0 | 0 | 0 | 0 |
| `other` | 0 | 0 | 0 | 0 | 1 |

By the group of the sampled claim:

| Sampled claims | Accepted |
|---|---|
| Candidates 1 to 120 | 4 of 6 (66.67%) |
| Candidates 121 to 240 | 10 of 12 (83.33%) |
| Candidates 241 to 360 | 9 of 12 (75.00%) |
| Candidates 121 to 360 (outside the first 120) | 19 of 24 (79.17%) |

By the reason the denial letter gives, as the model read it from the 30 letters:

| Denial reason of the letter | Accepted | Fourth measurement |
|---|---|---|
| `noncovered` | 12 of 13 | 11 of 17 |
| `other` | 10 of 13 | 8 of 8 |
| `medical_necessity` | 0 of 2 | 0 of 3 |
| `coordination_of_benefits` | 1 of 1 | 1 of 2 |
| `benefits_exhausted` | 0 of 1 | none in the sample |

The command exited with 0. **A model gave these 30 verdicts, not a person.** It was the same
model that gave the 120 verdicts of the first four measurements (see "Who scored").

**The decision rule is met:** 23 of 30 is at least 18 of 30. So the next step after this
work is merged is `/stage-release v0.5.0`.

**How firm the number is.** The model marked 10 of its 30 verdicts as borderline in its
notes: 8 accepts and 2 rejects. As written before scoring, they do not change the decision:

| Reading | Accepted | Why |
|---|---|---|
| The one recorded | **23 of 30 (76.67%)** | |
| The 8 borderline accepts are rejected | 15 of 30 (50.00%), below the target | Each is at the limit of a light edit, or has one sentence that goes past its quote |
| The 2 borderline rejects are accepted | 25 of 30 (83.33%) | Each has one wrong detail in an otherwise usable letter |
| The reading of reviews 1 and 2 (no `other` letter is accepted) | 13 of 30 (43.33%) | The 10 accepted `other` letters cite only the right to a second look |

The first two other figures are the model's own count from its notes. The last is worked
out from the counts above. None of them is a second review.

### Who scored

**A model gave the verdicts of all five measurements, not a person.** The model was
Claude, the coding assistant used to build this project. It read the same file a person
would read and applied the rubric above. The project owner decided on 2026-10-11 to record
this as the measurement, with a model acting in place of the reviewer. **No person has
scored any of the five samples.**

The second measurement was scored by the same model, after it had scored the first and knew
the result was 0 of 30. It also knew what the change between the two was meant to do. So
the second set of verdicts is not independent of the first.

So the number answers a narrower question than the done condition asks. It says what a
model found when it compared each draft with its denial letter and its cited text. It does
not say what a person who files appeals would accept.

The model read the rubric strictly: every draft that gives no argument for overturning the
denial was rejected, also when its facts were right. The model's own estimate is that a
lenient reader could accept about 6 of the 15 drafts without a citation, which would give
at most about 20%. For the second measurement the same estimate is again about 6 of 30:
the drafts with correct facts, no placeholder and no citation that does not fit. Both
figures are estimates, not measurements, and both are below the target.

**The third measurement was less independent than the first two.** The same model scored
again, and by then it:

- knew both earlier results (0 of 30 twice) and that the stage is released at 60%;
- had written the reading of the rubric that accepts an `other` letter;
- had written the rule of prompt `v2` that tells the drafting model to write exactly that
  letter (3.15);
- had written into prompt `v2`, as an example, the argument that all 10 accepted
  `noncovered` letters make (3.15);
- had proposed the search phrases that bring back the passages the letters cite.

The reading was fixed before any `v2` draft existed, so it was not bent to the drafts. But
the prompt was fitted to the reading, and the reviewer then judged the result of its own
rule. 7 of the 17 accepted drafts are `other` letters that only this reading accepts, and
the other 10 are `noncovered` letters that make the argument the prompt gives as its
example. So the rise from 0% to 56.67% is partly better drafts and partly an easier test,
and the two cannot be told apart from these numbers.

**The fourth measurement is no more independent than the third.** All five points above
still hold: the reading, the `other` rule, the example argument and the search phrases are
unchanged. And by then the same model also:

- knew the third result (17 of 30) and that 1 more accepted draft would have met the
  target;
- had read the 13 rejected drafts of the third review and written the three new prompt
  rules and the fact check from their faults;
- had proposed which values the drafting model is no longer sent.

Two things were done to keep the result from bending. The reading was not changed and was
written into the score file before any draft was read. And the sample was drawn with a new
seed from twice as many claims, so 17 of its 30 claims had never been in a loop run
before. On those 17 the share is 10 of 17 (58.82%), below the target; on the 13 from the
first 120 claims it is 10 of 13. The two groups are too small to say that the difference is
real. But it is the pattern one would see if the prompt had been fitted to the first 120
claims.

15 of the 20 accepted drafts are light edits, and 4 of those are borderline. So the fourth
number depends on where one reviewer drew the line of "at most 3 sentences".

**The fifth measurement is no more independent of the scorer than the fourth.** The same
model scored again. It knew all four earlier results, that the stage is released at 60%,
and it had written the prompt rules and the fact check. Three things were done so that the
result could not bend to that:

- **The claims were new to every review and every prompt rule.** No claim of the sample was
  in any earlier sample, so no prompt rule was written from its faults.
- **The rule that decides the release was written first**, with the reading of the rubric
  and the way the borderline verdicts are reported, before any draft was read.
- **Nothing was changed between the runs.** No prompt, no check and no search phrase was
  touched after the fourth measurement, so the fifth tests the same drafter.

What was not done: a second scorer, or a person. All 23 accepted drafts are light edits,
and 8 of them are borderline. So the fifth number still depends on where one reviewer drew
the line of "at most 3 sentences", and with all 8 rejected it is 50.00%.

The model's verdicts are in a local file of their own in each review folder. The score file
`export` wrote is still empty in all five, ready for a person.

### What the rejected drafts show

- **The cited text does not fit the service.** 14 of the 15 drafts with a citation quote a
  coverage determination that has nothing to do with the denied service: laser procedures
  (7 drafts), a determination about another body part (3), colorectal cancer screening (2),
  endoscopy (1) and clinical trials (1). The quote is word for word in the cited chunk, so
  the citation validator (3.15) passes it. The validator checks that a quote exists, not
  that it is about the claim.
- **A draft without a citation argues nothing.** 13 of the 15 only state the facts of the
  claim and ask for a review.
- **Smaller faults that repeat:** scan errors of the denial letter copied into a claim
  number or a name; placeholders such as `[Your Name]`; an invented payer address (2
  drafts); the internal category word `other` shown to the insurer; a line amount mixed up
  with the claim total.

Both main faults come from the search question (3.15), which then held one fixed phrase
per denial reason and the procedure codes, and no words about the service.

The points above are from the first measurement. The second shows what the minimum
similarity changed, as judged by the model:

- **Fewer drafts quote a text that does not fit.** 15 of the 30 sampled drafts carried a
  citation before, 6 do now, and `citation_does_not_fit` fell from 14 to 6.
- **A similarity of 0.55 does not make a citation fit.** All 6 sampled drafts with a
  citation still quote a determination that is not about the denied service: endoscopy,
  sterilization, colorectal cancer screening and laser procedures. The 14 citations of all
  56 drafts name only five determinations.
- **Most drafts now argue nothing.** `no_policy_argument` rose from 13 to 21. A claim with
  no evidence gets a letter that states the facts and asks for a review. The minimum takes
  a wrong argument away and puts no right one in its place.
- **The drafting faults are still there.** The drafting prompt did not change:
  placeholders, an invented street address (1 draft), the internal category word `other`
  and a claim total given as a line amount are in the new drafts too. Several drafts argue
  medical necessity or "not covered" against a denial the letter gives another reason for.

So the share did not move, as expected before the change was built. The corpus then had no
text about most denied services and none about appeal rights (3.13), and no search question
can find what is not there.

The third measurement, as judged by the model:

- **Every draft now makes a policy statement.** All 54 drafts carry a citation, each from
  a manual chapter. `no_policy_argument` fell from 21 to 1. The one left is a medical
  necessity letter that quotes the rule that services which are not reasonable and
  necessary are not covered, which supports the denial.
- **Wrong facts are now the main fault: 8 of the 13 rejects.** A claim total given as the
  amount appealed or as "the total charge" (2 drafts; one of them also adds a fact that
  is not in the letter); the category word `other` given as the denial's reason (2); a
  denial reason the letter does not give (2); a wrong line number (1); a patient name
  taken wrongly from a damaged name in the letter (1). Prompt `v2` has a rule against
  the first three, and the model did not always follow it.
- **A quote that is in the text can still be used for something it does not say.** 4
  drafts were rejected as `citation_does_not_fit`: the paragraph draws a conclusion the
  quoted passage does not support, and in one draft the passage says the opposite.
- **The placeholders and the invented addresses are gone.** 0 of the 30 drafts has text
  in square brackets (11 of the 30 notes of the second review named one), and none has an
  invented address. The code check refused 0 drafts, so the prompt rule did this.
- **The arguments are thin and all alike.** The 10 accepted `noncovered` letters use the
  same sentence of the same passage, and the 7 accepted `other` letters cite the same
  right to a redetermination. That is the cost of one question per denial reason (3.15).
- **Smaller faults that were not counted as a wrong fact:** several letters copy a damaged
  character from the noisy denial letter into a claim number or a name, as in reviews 1
  and 2; 3 accepted letters have no closing or no name; every letter speaks of Medicare
  terms, such as "the MAC", to a made-up payer. One accepted letter writes the category
  word `noncovered` for the reason and was scored as a light edit, while two letters that
  wrote `other` were rejected: there the word replaced the reason the letter gives.
- **Seen, not looked into:** one sampled claim was searched and drafted as a medical
  necessity denial, while its letter says the line "could not be paid as submitted".

The fourth measurement, as judged by the model:

- **The two wrong facts the new work aimed at are gone.** 0 of the 30 drafts gives a claim
  total, and 0 writes "other" as the reason (4 of 30 in the third review). The fact check
  refused 0 of the drafts of the run, so the new message did this: the model was not sent
  those values.
- **`wrong_fact` fell from 8 to 2.** One of the 2 is a patient name taken wrongly from a
  damaged name in the letter. It is the same claim as in the third review, and the fault
  comes from the extraction, not from the drafter. The other gives the reason of one
  denied line wrongly: it says both lines were denied as not covered, and the letter says
  one of them could not be paid as submitted. No code checks which reason belongs to which
  line (3.15).
- **A citation that does not fit is now the main fault: 7 of the 10 rejects**, up from 4.
  The paragraph says more than its quote, although prompt `v3` has a rule against exactly
  that. In 3 drafts the quote about analysing each exclusion is put under a request about a
  line that was denied for another reason, or is said to give a right to a review. In 1
  the paragraph turns the passage round. In 2 `medical_necessity` letters the paragraph
  states a duty of the payer that the quote does not hold. In 1 `coordination_of_benefits`
  letter it drops the limit the passage sets, and the sentence then argues for the denial.
- **No `medical_necessity` letter was accepted: 0 of 3**, after 0 of 1. Two of the three
  quote the rule that services which are not reasonable and necessary are not covered,
  which supports the denial. Prompt `v3` names that very passage as one never to cite, so
  the rule did not hold the model back here.
- **Every accepted `other` letter and every accepted `noncovered` letter makes the same
  argument as in the third review.** The 8 `other` letters cite the right to a
  redetermination. The 11 `noncovered` letters use the one sentence about analysing each
  exclusion. Nothing was built to widen either.
- **A claim with denied lines of several reasons is the hard case.** At least 4 of the 6
  rejected `noncovered` letters are about such a claim.
- **Smaller faults that were not counted as a wrong fact:** 3 letters copy a damaged
  character from the noisy denial letter into the payer's name; several have no closing or
  no name; letters still speak of "the MAC" to a made-up payer.

The fifth measurement, as judged by the model:

- **A citation that does not fit fell from 7 rejects to 1.** The one left is a
  `medical_necessity` letter: its paragraph states a rule the quote does not hold, and the
  sentence it puts in quotation marks is not the cited sentence. Several accepted drafts
  have one sentence that goes past its quote and were scored as a light edit; most of the
  8 borderline accepts are of that kind.
- **All 3 wrong facts come from the stored letter extraction, not from the drafter**
  (checked read-only against the extraction). In 2 drafts the extraction reads the denial
  as `medical_necessity` while the letter says the line "could not be paid as submitted",
  and the draft then argues medical necessity. In 1 the extraction took the list number of
  the denied line for its line number. The drafter wrote what it was given.
- **`no_policy_argument` rose from 1 to 3**, each a different kind of draft: a
  `medical_necessity` letter that quotes the rule which supports the denial; the one draft
  without a citation (a `benefits_exhausted` denial, whose phrase reaches no passage, 3.15),
  which only asks for details; and an `other` letter that cites the right to a review but
  never says the denial names no reason and never asks for it.
- **No `medical_necessity` letter was accepted: 0 of 2**, after 0 of 3 and 0 of 1. Together
  with the 2 wrong facts above, every draft argued as a necessity denial was rejected.
- **The accepted letters make the same two arguments as before.** The 10 accepted `other`
  letters cite the right to a redetermination; the 12 accepted `noncovered` letters use the
  sentence about analysing each exclusion. A letter about a claim with lines of several
  reasons was accepted twice, but its not-covered lines got no argument of their own.
- **Smaller faults that were not counted as a wrong fact:** several letters have no closing
  or no name; letters still call a Medicare manual the payer's "own policy" or ask "the MAC"
  to review.

### When the share is below 60%

The done condition stays as it is: the target is not lowered and not reworded. Stage 4 is
not released as `v0.5.0`. The most common reject reason picks the next piece of work, and
the share is then measured again with the same command. After the first measurement that
was the search (`citation_does_not_fit`). After the second it was `no_policy_argument`: the
CMS manual chapters, for evidence that applies to more claims, and a drafting prompt `v2`,
for the faults that repeat. Both are built, and the third measurement is their result.

After the third the most common reason was `wrong_fact`. So the next work was the facts of
a letter: the claim totals and the category words were taken out of the model's message,
the fact check refuses a draft that still has one, and prompt `v3` got three rules for the
citation faults (3.15). The fourth measurement is their result.

**After the fourth the share is at the target as scored: 20 of 30 (66.67%).** `score`
exits with 0. That is the first of the four measurements for which the rule of this
sub-section does not apply as written. The number was not firm (53.33% to 76.67% by the
borderline verdicts), a model scored, and on the 17 new claims the share was 58.82%. So the
project owner did not release on it and had the share measured once more, on claims no
review had read, with the decision rule written first.

**After the fifth the decision rule is met: 23 of 30 (76.67%).** `score` exits with 0, and
the next step is `/stage-release v0.5.0`. The release does not mean the drafts are done.
Still not built:

- an argument for a `noncovered` denial that is more than one sentence of one passage;
- any argument for a `medical_necessity` denial (none accepted in any measurement);
- a check that a paragraph says no more than its quote, which no code can do today;
- a check of the reason of each denied line, and a fix for the extraction faults behind a
  wrong reason, a wrong name or a wrong line;
- a search phrase that reaches a passage for a `benefits_exhausted` denial (3.15).

A new measurement needs a new export into another folder, because a draft that was written
again has a new fingerprint.

### Known limits

About who scored:

- A model judged letters a model wrote. It can share the blind spots of the model that
  drafted them, and it is not the reviewer the done condition names.
- One reviewer gave every verdict, and no second reviewer checked any of them.
- Nothing ties the score file to a reviewer. It is a local file that can be changed later,
  and nothing records who filled it in or when.
- The second measurement was scored by the same model that had scored the first and knew
  its result. A reviewer who expects no change is more likely to find none.
- 29 of the 30 claims are in both of the first two samples, but each has a new draft,
  written by a model whose answer changes from call to call. A difference between the two
  measurements is partly the minimum and partly that chance.
- The third measurement was scored by the same model again. It knew both earlier results,
  wrote the new reading for `other` letters and wrote the prompt rule that fits it (see
  "Who scored").
- **The four shares cannot be compared as one series.** The third and the fourth used an
  easier reading for `other` letters. With the reading of the first two the third is 10 of
  30 (33.33%) and the fourth 12 of 30 (40.00%).
- **The third and the fourth share cannot be compared directly either.** They have the
  same reading but not the same sample: seed 42 from 54 drafts of 120 claims, then seed 43
  from 106 drafts of 240 claims. Only 6 claims are in both. A rise from 17 to 20 of 30 is
  within what two samples of 30 give by chance.
- The third share depends on single judgement calls. One draft more would meet the target,
  and one way of reading the 10 `noncovered` letters takes it down to 23.33%.
- How many claims the third sample shares with the first two was not recorded.
- The fourth measurement was scored by the same model again. It knew the three earlier
  results, and it wrote the prompt rules and the fact check from the rejects of the third
  (see "Who scored").
- The fourth share depends on 7 verdicts the model itself marked as borderline. They move
  it from 53.33% to 76.67%, across the target. No second reviewer looked at them.
- The fourth share is above the target only with the sampled claims of the first 120
  counted in. On the 17 new claims alone it is 58.82%.
- The fifth measurement was scored by the same model again, knowing the four earlier
  results and that the release depended on this one (see "Who scored").
- The fifth share depends on 10 verdicts the model itself marked as borderline. They move
  it from 50.00% to 83.33%. By the rule written before scoring they do not change the
  decision, but no second reviewer looked at them.
- **The five shares cannot be compared as one series.** With the reading of reviews 1 and
  2 the fifth is 13 of 30 (43.33%).

About the earlier measurements' files:

- The drafts of the first measurement are overwritten in `appeal_drafts` (3.16). Their
  text is only in the local file the reviewer read, which is not committed.
- Its score file can no longer be scored with the command. Every one of its 30 drafts was
  written again, so `score` refuses each row as "drafted again". The recorded 0 of 30
  cannot be reproduced from the database.
- The drafts of the second measurement are still stored, but its score file can no longer
  be scored either: the command reads only the drafts of the current agent version and
  drafting prompt version.
- The same holds for the third measurement since agent `v4` and prompt `v3` are the
  defaults: its drafts are still stored, but `score` refuses its file. The fourth and the
  fifth folder can both be scored today, because both hold drafts of agent `v4` and prompt
  `v3` and no claim of either was drafted again.

About the sample:

- 30 drafts is a small sample. A measured 0 of 30 still fits a true share of up to about
  12% for the same reviewer. A measured 60% would stand for a true share of about 42% to
  75%, the measured 17 of 30 for about 39% to 73%, the measured 20 of 30 for about 49%
  to 81%, and the measured 23 of 30 for about 59% to 88%. So the fourth sample alone cannot
  show that the true share is at 60% or above, and the fifth only just misses showing it.
- The drafts (55 in the first measurement, 56 in the second, 54 in the third) come from
  the first 120 test claims in claim id order, the 106 of the fourth from the first 240 and
  the pool of 90 of the fifth from the first 360, not from a draw over all 749 test claims
  that can be taken.
- The pool of the fifth mixes drafts from two loop runs: 46 drafted on 2026-10-12 and 44
  on 2026-10-15, with the same agent, prompt and search question versions and the same
  model name. Nothing checks that the provider's model behind that name stayed the same
  between the two days.
- The fifth sample is drawn from the drafts no review had read. Of the 150 drafts of agent
  `v4`, the 60 read before are left out on purpose, so the pool is not every draft of the
  drafter.
- `score` checks the number of rows and that each row belongs to a draft that can be
  reviewed. It does not prove the rows are the sample of the seed that was named.
- The fourth sample holds 3 `medical_necessity` letters and 2 `coordination_of_benefits`
  letters, and none for a `duplicate` or `benefits_exhausted` denial. The fifth holds 2,
  1, none and 1. The shares say almost nothing about those reasons.

About the data:

- The claims, the people and the documents are made up. The share says nothing about real
  appeals.
- The policy text is from 2026 and the claims are from 2008 to 2010 (3.13).
- The payers are made up, and the policy text is Medicare policy. Since the third
  measurement every cited passage is a Medicare manual rule, so each letter argues a
  Medicare rule to a payer that is not Medicare. The model did not reject a draft for
  that.
- The letters argue a general rule for the denial reason, never the denied service (3.15).
  A reviewer who files real appeals may not count that as an argument at all.

About the rubric:

- "At most 3 sentences" is the reviewer's judgement. No edit is made, so nothing measures
  it.
- A draft gets one reject reason. A draft with two faults shows only the one the reviewer
  picked.

## 3.18 API and auth

The API is the HTTP surface the dashboard will call. This first part has sign-up, login,
logout, the logged-in user's own record, one admin route and a health check. The upload and
job routes are in 3.19, the worklist, the draft review, the audit log and the overview in
3.20.

Three things hold for every route:

- **The login token is only ever in a cookie.** It is an HttpOnly cookie, so page scripts
  cannot read it, and no response body carries it.
- **The account comes from the logged-in user.** Every query takes its `account_id` from
  `current_user`, never from the path, the query string or the body
  ([conventions 6.4](06-conventions.md#64-database)).
- **Another account's row is "not found".** A row of another account is answered with `404`,
  the same as a row that does not exist, so the answer does not say that the row exists.

No route calls a model and no route spends money while the request is answered. The one
place money is spent is the background job an upload queues, after its answer is sent
(3.19).

### Where the code is

| File | Holds |
|---|---|
| `src/api/app.py` | `create_app(api_settings=None, session_factory=None, job_runner=None)`, the app factory; it puts the settings, the session factory and the job runner on `app.state`, answers a `422` with `answer_validation_error`, and at startup marks unfinished jobs `interrupted` (3.19) |
| `src/api/dependencies.py` | `get_api_settings`, `get_db_session` used through `DbSession` (commits when the route returns, before the answer is sent; rolls back on any error), `current_user`, `CurrentUser`, `require_role(*roles)` and the cookie name `AUTH_COOKIE_NAME` (`access_token`) |
| `src/api/passwords.py` | `hash_password`, `verify_password`, `verify_dummy_password`, the length limits 12 and 128 |
| `src/api/tokens.py` | `issue_token`, `read_token`, `TokenClaims` and `InvalidTokenError` |
| `src/api/schemas.py` | The bodies: `SignupRequest`, `LoginRequest`, `UserResponse`; `normalize_email`; `MAX_LOGIN_PASSWORD_CHARACTERS` (1,024) |
| `src/api/signup.py` | `create_account_with_admin`, `AccountNameTakenError`, `EmailTakenError` |
| `src/api/login.py` | `authenticate`, the login lookup by email |
| `src/api/user_lookup.py` | `find_user_response`: one user of one account, with the account's name |
| `src/api/routes/` | `auth.py`, `me.py`, `users.py`, `health.py`: one router each |
| `src/config/settings.py` | `ApiSettings` and `load_api_settings` |
| `src/db/migrations/versions/rev_0015_users_password_and_global_email.py` | Migration 0015: `users.password_hash`, email unique across all accounts, a check that the email is stored lowercased with no whitespace at either end; the downgrade restores the key per account and drops the hashes |

### The routes

| Method and path | Who may call it | Answers |
|---|---|---|
| `POST /auth/signup` | anyone | `201` with the new user and the login cookie; `409` when the account name or the email is taken; `422` for a body that does not fit |
| `POST /auth/login` | anyone | `200` with the user and the login cookie; `401` for an unknown email or a wrong password (one message for both); `422` for a body that does not fit |
| `POST /auth/logout` | anyone | `204`; the cookie is cleared |
| `GET /me` | a logged-in user | `200` with the user and their account's name; `401` when not logged in |
| `GET /users/{user_id}` | an `admin` | `200` with that user of the admin's own account; `401` when not logged in; `403` for another role; `404` for a user of another account or no user at all |
| `GET /health` | anyone | `200` with `{"status": "ok"}`; it touches no database |

`GET /users/{user_id}` exists mainly to prove the isolation: its tests sign up two accounts
and show that the admin of one gets `404` for the user of the other.

The roles are `viewer`, `reviewer` and `admin` (the enum `user_role` since migration 0001).
`GET /users/{user_id}` and `GET /audit` (3.20) check for `admin`; `POST /uploads` (3.19) and
the edit, approve and reject of a draft (3.20) let `reviewer` and `admin` in and answer `403`
to a `viewer`.

### Sign-up, step by step

1. The body is checked: the account name is trimmed and 1 to 200 characters; the email is
   trimmed, lowercased and must look like an email (one `@`, text on both sides, no
   whitespace, at most 320 characters); the password is 12 to 128 characters and is not
   trimmed. Only the length is checked, following NIST SP 800-63B.
2. The password is hashed with Argon2id (`argon2-cffi`). The hash is salted, so two users
   with the same password get different hashes.
3. The account and its first user, an `admin`, are written inside one savepoint. When the
   name or the email is taken, the database's unique key refuses the row
   (`accounts_name_key` or `uq_users_email`), both rows are rolled back and the answer is
   `409`. Sign-up never searches other accounts' rows to find out.
4. The user is logged in: the login cookie is set in the same answer.

### Login, step by step

1. The email is trimmed and lowercased, as at sign-up. The password length is not checked
   against 12-128, so a password of the wrong length gets the same `401` as a wrong one. A
   password over 1,024 characters (`MAX_LOGIN_PASSWORD_CHARACTERS`) gets that same `401`
   without being hashed, so a huge body costs no Argon2 work.
2. The user is looked up by email alone. This is the one query on `users` without an
   `account_id` filter: before login the account is not known. It is safe because an email
   belongs to exactly one account (`uq_users_email`) and nothing is returned unless the
   password matches.
3. For an unknown email the password is still checked against a fixed dummy hash, so that
   case takes about as long as a wrong password and the time does not tell which emails exist.
4. A match sets the login cookie; anything else is `401` with the message "email or password
   is wrong".

Hashing and checking take tens of milliseconds on purpose: that is what makes a stolen hash
slow to guess.

### The login token

| Part | Value |
|---|---|
| Format | JWT signed with HS256 (`PyJWT`); a token of any other algorithm is refused |
| Claims | `sub` (the user id, as text), `acct` (the account id), `iat`, `exp`; all four are required |
| Lifetime | `AUTH_TOKEN_TTL_MINUTES`, 60 by default; there is no refresh token |
| Cookie | `access_token`, HttpOnly, SameSite=Strict, path `/`, `max_age` equal to the lifetime, `Secure` unless `AUTH_COOKIE_SECURE` is false |

A missing, damaged, expired or wrongly signed token, or one whose claims have the wrong type,
raises one `InvalidTokenError`, and the route answers `401` "not logged in". The answer does
not say which check failed. The error message never repeats the token.

SameSite=Strict means the browser does not send the cookie with a request started from
another site, which is what stops a forged cross-site request.

### How one account is kept apart from another

`current_user` runs on every route that needs a login:

1. It reads the cookie and checks the token.
2. It looks the user up with `for_account(User, acct).where(User.id == sub)`: the user is
   searched only inside the token's account. A token whose user and account do not belong
   together finds nobody and gets `401`, like a missing token.
3. It returns `CurrentUser` (user id, account id, email, role), read from the database on
   every request, so a deleted user is logged out at once.

A route then passes `user.account_id` to every query. `require_role(...)` wraps
`current_user` and answers `403` when the user's role is not one of the allowed ones.

### Settings

| Key | Required | Meaning |
|---|---|---|
| `AUTH_TOKEN_SECRET` | yes | The key that signs the tokens; at least 32 characters after trimming; hidden in error messages |
| `AUTH_TOKEN_TTL_MINUTES` | no (default 60) | How long one token stays valid, at least 1 |
| `AUTH_COOKIE_SECURE` | no (default true) | Whether the cookie is sent only over HTTPS; set false only for local development over plain HTTP |

The keys are in `ApiSettings`, not in `Settings`, and are loaded only when the app is built.
The pipelines and their tests need no token secret. A missing or short secret fails when the
app starts, with a message that names the key. The database URL is read through the main
`Settings` (`DATABASE_URL`), so a missing required key there fails at the start too.

### Starting it

```
uvicorn src.api.app:create_app --factory
```

`--factory` makes uvicorn call `create_app()`, which reads the settings and the database URL
from the environment and builds the job runner (3.19), so the LLM keys, the prompt files and
the model file must be there too. Tests call `create_app(api_settings, session_factory,
job_runner)` with their own values instead, the runner a stub. The route tests run the real app against `app_test` on a connection that is
rolled back after each test.

Every route takes its session through `DbSession`, which closes the session (and commits)
when the route returns, before the answer is sent (`scope="function"`). FastAPI's default
closes it after the answer, so a client could be told `201` for a sign-up whose commit then
failed. A failed commit is now a `500` with no login cookie (tested).

### Logging

Sign-up and login log the account id and the user id. A failed login logs only that it
failed. The email, the password, its hash and the token are never logged, and no response
carries the password or the hash. A `422` names each bad field (`loc`, `type`, `msg`) but
never repeats the value that was sent: FastAPI's own answer does (`input`), which for a
password would be the password, so `create_app` replaces it with `answer_validation_error`.
A database error never shows the values of its query either: the engine is created with
`hide_parameters=True` (`create_db_engine`), so a logged traceback of a failed sign-up does
not carry the email or the hash. This holds for the pipelines too.

### Known limits

- **No server-side logout.** `POST /auth/logout` clears the cookie, but a copied token stays
  valid until it expires (at most `AUTH_TOKEN_TTL_MINUTES`).
- **No rate limit on login.** Argon2id makes each guess slow, but nothing stops many
  guesses. Planned with the deployment (Stage 6).
- **One email, one account.** A person who works for two accounts needs two emails.
- **No invite and no role change yet.** Every signed-up user is the `admin` of a new
  account; adding users to an account is a later branch.
- **Isolation is enforced in the API code, not by the database.** A query that forgets the
  `account_id` filter is not stopped by Postgres. Row-level security would stop it, at the
  cost of much more setup.

## 3.19 Uploads and jobs

A user uploads one denial letter. The API stores its text, answers at once with a job id, and
a background job turns the letter into a draft: it extracts the fields (3.9), runs the agent
loop (3.16) and stores the result in the account's own tables. The user reads the job's
status with `GET /jobs/{id}`.

Three things hold for every upload:

- **The account comes from the logged-in user.** Both routes and every query of the job take
  `account_id` from `current_user`, and a job of another account is answered with `404`
  (3.18, [conventions 6.4](06-conventions.md#64-database)).
- **No document text reaches a job row, an answer or a log.** A failure is stored as one of
  six fixed codes; a log line gives the job id, the account id and an error's class name,
  never its message, the file name or an extracted value.
- **Every job ends in a stop for a person.** A finished job holds the loop's end state; none
  of them means "approved" or "sent" ([conventions 6.8](06-conventions.md#68-hard-rules)).

The letters used so far are made up, so no real patient data is involved.

### Where the code is

| File | Holds |
|---|---|
| `src/api/routes/jobs.py` | `POST /uploads` and `GET /jobs/{job_id}`; the answers `UploadAccepted` and `JobResponse` |
| `src/api/upload_limit.py` | `UploadSizeLimit`, the middleware that checks the declared size of `POST /uploads` before its body is read |
| `src/api/upload_file.py` | `read_uploaded_letter(data)`: the file type from the first bytes and the text, or `UploadFileError` with a fixed message |
| `src/ingest/pdf_text.py` | `read_pdf_bytes(data, name)`: the text of every page of a PDF held in memory (the reader of 2.3.2, for bytes) |
| `src/jobs/store.py` | `NewUpload`, `create_upload_job`, `find_job`, `start_job`, `read_upload_text`, `save_upload_extraction`, `finish_job_done`, `finish_job_failed`, `mark_interrupted_jobs`; `JobNotFoundError`, `JobStateError` |
| `src/jobs/run.py` | `run_upload_job(...)`, one job from `queued` to its end; `JobContext`, what every job runs with; `UploadLetter`, the letter as the loop reads it |
| `src/jobs/context.py` | `build_job_context(...)`: the gateway, the default letter extraction and drafting prompts and the win model, loaded once |
| `src/ml/features.py` | `build_letter_features(letter)`: the three model features from the letter's extracted fields |
| `src/api/app.py` | `build_job_runner`, and the startup step that marks unfinished jobs `interrupted` |
| `src/db/models.py` | `Upload`, `Job`, `UploadExtraction`, `UploadAgentRun`, `UploadDraft` and the enums `UploadFileType`, `JobStatus`, `JobFailure` |
| `src/db/migrations/versions/rev_0016_uploads_and_jobs.py` | Migration 0016: the five tables and the types `upload_file_type`, `job_status`, `job_failure`; the downgrade drops them and keeps `agent_end_state` and `llm_provider`, which older tables use |

`src/jobs/` calls only public functions of the other packages: `extract_document` (3.9),
`run_agent_for_claim` (3.16) and `build_letter_features` (3.10).

### The routes

| Method and path | Who may call it | Answers |
|---|---|---|
| `POST /uploads` | a `reviewer` or an `admin` | `202` with the job id and status `queued`; `401` when not logged in; `403` for a `viewer`; `411` without a `Content-Length` header or with a `Transfer-Encoding` header; `400` when that header is not a whole number; `413` for a body or a file over the limit (step 1); `422` for a file that cannot be read as a letter (one fixed message per case, below) |
| `GET /jobs/{job_id}` | a logged-in user of any role | `200` with the status, the failure code (only when `failed`), the end state (only when `done`) and the three times; `401` when not logged in; `404` for a job of another account or no job at all |

The upload is a form field `file` (`multipart/form-data`, read by `python-multipart`).

### An upload, step by step

1. Before the body is read, and before the login check, the middleware `UploadSizeLimit`
   reads the size the client declares in `Content-Length`. No such header, or any
   `Transfer-Encoding` header (a chunked body, framed by its chunks and not by the declared
   size), is `411`; a header that is not a whole number is `400`; a size over
   `UPLOAD_MAX_BYTES` + 16,384 bytes (room for the form around the file) is `413`. Without
   `Transfer-Encoding` the server never reads past the declared size, so a huge body is refused
   without being received; FastAPI would otherwise write all of it to a temporary file first.
   The path is compared without the app's `root_path`, as the router compares it. Then at most `UPLOAD_MAX_BYTES` + 1 bytes of the file are read, and
   a file over the limit is `413`.
2. The file name must be 1 to 255 characters after trimming, or `422`. It is stored, never
   logged and never answered.
3. The type is decided from the first bytes, never from the name or the browser's content
   type: a file that starts with `%PDF-` is read as a PDF, any other as UTF-8 text.
   - A PDF's pages are joined with a blank line. A PDF that cannot be opened, is encrypted or
     holds no text (a scan) is `422`. There is no OCR.
   - A text file that is not UTF-8 is `422`. A UTF-8 byte order mark is dropped.
   - An empty file, text that is only whitespace, and text with a NUL character (Postgres
     refuses one) are `422`.
4. One `uploads` row (the name, the type, the size, the text and its SHA-256) and one `jobs`
   row with status `queued` are written. The file itself is not kept: only its text.
5. The job is handed to FastAPI's `BackgroundTasks`. It runs after the answer is sent, by
   which time `DbSession` has committed the queued job (3.18). The answer is `202`.

### A job, step by step

`run_upload_job(session_factory, context, *, account_id, job_id, today)` runs one job. "Today"
is the date the job runs on.

| Step | What it does | Costs money |
|---|---|---|
| 1. Start | Moves the job from `queued` to `running`. A job that is not `queued` (already run, or of another account) is left alone and nothing is done | no |
| 2. Extraction | Calls `extract_document` with the letter prompt of `EXTRACTION_PROMPT_VERSION` on the stored text. The fields are stored in `upload_extractions` at once, in their own commit | one model call |
| 3. Features | `build_letter_features`: the denial reason, the total allowed charge, and "fully denied" when the total paid is $0. None when the reason or an amount is missing or an amount is below zero | no |
| 4. The loop | `run_agent_for_claim` with an `UploadLetter`: rules, score, evidence, draft (3.16). No features means no score: the loop ends `needs_review_no_draft` | one embedding and one or two drafting calls, only past the rules and the threshold |
| 5. Finish | Moves the job from `running` to `done` with the loop's end state, and stores the trace in `upload_agent_runs` and the checked letter, if any, in `upload_drafts`, in one commit; stores the job's cost in `jobs.cost_usd` | no |

The whole job runs inside the gateway's cost meter (3.8), so `jobs.cost_usd` is what its model
calls cost. A `failed` job stores its cost too; an `interrupted` one keeps 0.

A job is `done` whatever the loop's end state: a letter the rules stop is a finished job with
end state `stopped_by_rules`. `failed` means the job itself could not finish.

Each move happens only from the status before it (`queued` → `running` → `done` or `failed`),
in one `UPDATE ... WHERE status = ...`, so a job never runs twice. The times come from the
database clock. Checks in the table keep the columns consistent: a failure code only when
`failed`, an end state only when `done`, the times set to match the status.

### Job failures

| Code | When |
|---|---|
| `extraction_failed` | The extraction answer was cut off or did not fit the schema |
| `budget_exceeded` | The monthly LLM budget is reached (3.8) |
| `llm_unavailable` | No provider could answer |
| `evidence_not_ready` | The evidence has no stored vector (3.14) |
| `interrupted` | The API process stopped while the job was `queued` or `running` |
| `internal_error` | Any other error |

A background job has no caller to raise to, so every error ends the job as `failed` with one
of these codes. Only the code is stored: never the error's message, which can quote the
document or the model's answer. The extraction of a job that failed later stays stored.

**Unfinished jobs at startup.** A job runs inside the API process, so a stopped process
leaves its jobs `queued` or `running` forever. Before the app takes its first request, the
startup step (`mark_interrupted_jobs`) fails every such job with `interrupted`, account by
account, so every query on `jobs` filters on `account_id`. The user uploads the letter again.

### The tables

All five are business tables with `account_id` (index, not null, foreign key to `accounts`).

| Table | One row per | Holds |
|---|---|---|
| `uploads` | upload | the user, the file name, `file_type` (`text` / `pdf`), the size in bytes, the text and its SHA-256 |
| `jobs` | upload (unique) | `status`, `failure`, `end_state`, `started_at`, `finished_at`; since migration 0017 `cost_usd` (3.20) |
| `upload_extractions` | job (unique) | prompt version, model, provider, the hash of the text read, the fields as JSON |
| `upload_agent_runs` | job (unique) | agent version, end state, the hash of the text, the trace as JSON |
| `upload_drafts` | job (unique) | prompt version, search question version, model, provider, the hash of the text, the checked letter as JSON; since migration 0017 its review (3.20) |

**Every link carries `account_id`.** Each foreign key between these tables, and the one from
`uploads` to `users`, is on the pair (`account_id`, id), backed by a unique key on that pair
(`uq_users_account_id`, `uq_uploads_account_id`, `uq_jobs_account_id`). So a job cannot point at
another account's upload, and an upload cannot name another account's user: the database
refuses it (tested). The results hang off the job and are deleted with it; a job is deleted
with its upload. The link from `uploads` to `users` has no cascade.

The results take the place of the planned `extractions`, `predictions` and `drafts` (3.4) for
an upload. The draft's citations name evidence the same way as in 3.15, never by row id.

### Settings

| Key | Required | Meaning |
|---|---|---|
| `UPLOAD_MAX_BYTES` | no (default 1,000,000) | The largest file one upload may send, in bytes, at least 1 |

It is in `ApiSettings` (3.18). A job also uses the keys of the gateway (3.8), the prompt and
model folders, `RULES_AMOUNT_FLOOR_USD` (3.11) and `AGENT_WIN_THRESHOLD` (3.16). `create_app()`
builds the job runner when the app starts, so a missing LLM key, prompt file or model file
fails there, with a message that names it, and not at the first upload.

### Measured

The first "upload to draft ready < 2 minutes" measurement (2026-10-16): `uvicorn` on the local
machine, `app` at migration 0016, one made-up account, two made-up `medical_necessity` letters
(appeal deadline in 2027), each uploaded as a text file. A free check first showed that a
fully denied `noncovered` letter scores 0.17 and would stop at the threshold, so the letters
are `medical_necessity` (0.71).

| | Job 1 | Job 2 |
|---|---|---|
| `202` to `done` | 30.1 s | 29.7 s |
| Of which | extraction 6.1 s, retrieval 6.2 s, drafting 17.8 s | |
| End state | `draft_failed` (citation check `blocked`, a quote not in the evidence) | `draft_failed` (answer not parseable) |
| Extraction | all 15 fields right | |
| Evidence | 5 `cms_manual` matches, similarity 0.61 to 0.66 | |

Six model calls cost $0.002186 in all (`llm_calls`).

**The time target is met for the job (about 30 s of 120 s), but no draft came out.** Both
jobs ended `draft_failed`, so "upload to draft ready" is not yet measured for a job that ends
with a draft. Two letters are too few to say how often an upload is drafted. The second
measurement, with a draft, is in 3.20.

### Known limits

- **A job runs in the API process.** A restart fails its jobs as `interrupted`, and the user
  uploads again. There is no retry, no queue shared by several processes and no limit on how
  many jobs run at once.
- **The API must run as one process.** Each start marks every unfinished job of every account
  `interrupted`, so a second process (`--workers` above 1, or an old and a new process
  overlapping in a restart) fails the jobs the other one is still running. Such a job still
  spends its model calls, and its result is not stored.
- **No OCR.** A scanned PDF holds no text and is refused at upload.
- **One letter per upload.** The clinical note and the prior-authorisation record are not
  uploaded or read; the letter type is not checked, the file is read as a denial letter.
- **The score of an upload comes from the letter alone** (step 3), while the model was
  trained on features from claim rows (3.10). A wrong extracted amount changes the score.
- **The raw file is not kept.** Only its text is stored, so a PDF cannot be shown again as
  it was.
- **The text is stored in plain form in Postgres.** Encryption at rest and an access log
  ([02-data.md 2.7](02-data.md#27-privacy)) come with the deployment (Stage 6).
- **`GET /jobs/{id}` gives only the status and the end state.** The draft and everything
  else of a job are read with `GET /jobs/{id}/detail` (3.20).
- **Nothing is sent to an insurer.** A finished job always stops for a person.

## 3.20 Draft review, audit log and costs

A person reads the account's jobs in a worklist, opens one job to see the letter, what was
read from it, the agent's trace and the draft, and then edits, approves or rejects the draft.
Every action is written to an audit log. An overview gives the counts, what the jobs cost
and how long they took.

Four things hold for every route here:

- **The account comes from the logged-in user**, and a job of another account is answered
  with `404`, the same as a job that does not exist (3.18,
  [conventions 6.4](06-conventions.md#64-database)). For edit, approve and reject the `404`
  is decided before the role, so a `viewer` cannot probe for other accounts' jobs.
- **No draft is approved unless every `policy` paragraph has a valid citation.** Every edit
  and every approve checks the letter again ([conventions 6.8](06-conventions.md#68-hard-rules)).
  The check follows the paragraph kinds; see the known limits.
- **No route calls a model and none spends money.**
- **Nothing is sent to an insurer.** "Approved" is a status in the database. Nothing in the
  code sends a letter anywhere.

### Where the code is

| File | Holds |
|---|---|
| `src/api/routes/review.py` | `GET /worklist`, `GET /jobs/{job_id}/detail`, `PUT /jobs/{job_id}/draft`, `POST /jobs/{job_id}/draft/approve` and `/reject`; the bodies `EditDraftRequest`, `ApproveDraftRequest`, `RejectDraftRequest` and the refusal `LetterRefusal` |
| `src/api/routes/audit.py` | `GET /audit` |
| `src/api/routes/overview.py` | `GET /overview` |
| `src/review/actions.py` | `edit_draft`, `approve_draft`, `reject_draft`, `read_latest_draft`; `DraftNotFoundError`, `DraftNotPendingError`, `StaleDraftVersionError`, `LetterCheckError`; `MODEL_DRAFT_VERSION` (0) |
| `src/review/audit.py` | `record_audit_event` and `AuditDetail`, the only things an event may say |
| `src/review/audit_log.py` | `list_audit_events`, `AuditEntry`, `AuditPage` |
| `src/review/worklist.py` | `list_worklist`, `WorklistItem`, `WorklistPage` |
| `src/review/detail.py` | `read_job_detail`, `JobDetail`, `DraftDetail` |
| `src/review/overview.py` | `read_overview`, `Overview` |
| `src/review/paging.py` | Keyset paging shared by the worklist and the audit log; `DEFAULT_PAGE_SIZE` 50, `MAX_PAGE_SIZE` 100 |
| `src/drafting/letter_check.py` | `check_letter(letter, chunks, fields)`: a stored letter checked again; `LetterProblem`, `LetterProblemKind` |
| `src/drafting/cited_chunks.py` | `read_cited_chunks(session, letter)`: the stored chunks a letter cites |
| `src/drafting/edit_check.py` | `find_edit_problems(original, edited)`: only changes an edit may make |
| `src/llm/gateway.py` | `LlmGateway.meter()` and `CostMeter` (3.8) |
| `src/db/migrations/versions/rev_0017_draft_review_and_audit.py` | Migration 0017: the review columns of `upload_drafts`, `draft_revisions`, `audit_events`, `jobs.cost_usd` and the types `draft_review_status`, `draft_reject_reason`, `audit_action`; the downgrade removes all of it |

### The routes

| Method and path | Who may call it | Answers |
|---|---|---|
| `GET /worklist` | a logged-in user of any role | `200` with one page of the account's jobs, newest first; filters `status` and `review_status`; `before` + `limit` (1 to 100, default 50) |
| `GET /jobs/{job_id}/detail` | any role | `200` with the upload text, the extracted fields with their confidence, the trace, the model's draft, the latest version and the review; `404` for a job of another account or no job |
| `PUT /jobs/{job_id}/draft` | a `reviewer` or an `admin` | `200` with the new version; `404` (job, then draft); `403` for a `viewer`; `409` when the draft is decided or `based_on_version` is not the latest; `422` when the letter fails the checks |
| `POST /jobs/{job_id}/draft/approve` | a `reviewer` or an `admin` | `200` with `approved`; the same `404` / `403` / `409` / `422` as an edit; `version` must be the latest |
| `POST /jobs/{job_id}/draft/reject` | a `reviewer` or an `admin` | `200` with `rejected`; `404` / `403`; `409` when already decided; `reason` is one of the rubric's five values (3.17), never free text |
| `GET /audit` | an `admin` | `200` with one page of the account's events, newest first; `403` for another role |
| `GET /overview` | any role | `200` with the counts, the costs and the average time |

A worklist line has no document text: the file name, who uploaded it and when, the job's
status, failure and end state, the review status, the denial reason and the win probability.
The reason and the score are read out of the stored extraction and trace inside the
database, so neither the fields nor the trace leave it for the list.

**Keyset paging.** A page holds the rows with an id below `before`, newest first; a full page
gives `next_before`, the last id on it. Unlike offset paging, a job uploaded while a person
pages does not shift the rows they have not seen yet.

A refused letter is answered `422` with the message "the letter did not pass the checks" and
its problems: each one a kind, the paragraph's position and the citation's position. It never
repeats letter, quote or chunk text.

### The review, step by step

A draft is `pending` until it is approved or rejected. Both are final: a decided draft cannot
be edited or decided again (`409`). Version 0 is the model's draft, which never changes; each
edit stores the next version in `draft_revisions`.

1. **Lock.** Each action first locks the draft row (`SELECT ... FOR UPDATE`, filtered by
   `account_id`). Two reviewers acting at once are served one after the other, and the second
   one's version is then stale (`409`).
2. **Edit** (`PUT`). The body is the whole edited letter and the version it was based on,
   which must be the latest. The letter is first compared with the model's draft
   (`find_edit_problems`): a paragraph's text may change, and a paragraph or a citation may
   be removed; a paragraph's kind may not change, and no paragraph or citation may be added
   or moved. A new quote would need a new search, which is a model call. Then the letter is
   checked (`check_letter`, below). Only a letter that passes is stored as the next version.
3. **Approve.** The version approved must be the latest. Its letter is checked again, since a
   chunk may have been reloaded since the edit. The draft then records who, when and the
   approved version.
4. **Reject.** One rubric reason; the draft records who, when and why.
5. **Audit.** Each action adds its audit event in the same transaction, so a change without
   its event cannot be committed.

**The check of a stored letter** (`check_letter`, pure). A letter in `upload_drafts` or
`draft_revisions` names each chunk by its values, not by a search label, so the validator of
3.15 cannot read it. The check applies the same rules:

1. every `policy` paragraph has at least one citation;
2. every citation names a chunk that is still stored with the same `text_sha256`
   (`chunk_not_found` otherwise);
3. every quote has at least 20 characters and is in its chunk under the validator's rules
   (whitespace collapsed, whole words; `quote_is_in_evidence`, shared with 3.15);
4. no text in square brackets (`placeholder_text`, 3.15);
5. no wrong dollar amount and no "other" as the reason (the fact check of 3.15, against the
   job's stored extraction).

The kind `edit_not_allowed` comes from the edit check.

### The audit log

| Action | Written by | Detail |
|---|---|---|
| `account_created` | sign-up (3.18) | none; the only event with no job |
| `upload_created` | `POST /uploads` (3.19) | none |
| `draft_edited` | an edit | the new version |
| `draft_approved` | an approve | the approved version |
| `draft_rejected` | a reject | the reject reason |

An event holds the account, the user, the action, the job, the time and a detail with fixed
keys only (`AuditDetail`: `version`, `reject_reason`). It never holds document text, letter
text or a file name. Sign-up and the upload write their event in the same transaction as
their rows. The code only inserts events; nothing updates or deletes one.

The links from an event to its user and its job have no cascade, so an audited user or job
cannot be deleted on its own: the database refuses it. `GET /audit` gives the events with the
user's email, for the account's admins only.

### Job costs

`llm_calls` is an operational table without `account_id` (3.8), so it cannot say what one
account spent. Each job stores its own cost instead: the job runs inside the gateway's cost
meter, and its total goes into `jobs.cost_usd` (`Numeric(12, 6)`, default 0, never below 0)
when the job ends `done` or `failed` (3.19). `llm_calls` is unchanged.

`GET /overview` gives, for the account:

- the jobs by status, the `done` jobs by end state and the drafts by review status, every
  value present (0 when none);
- `cost_usd_this_month` (the jobs uploaded this month, by the database clock) and
  `cost_usd_total`, exact decimals;
- `average_seconds`, from upload to job end over the jobs that started and ended (None when
  there is none).

### The tables

All three are business tables with `account_id`.

| Table | One row per | Holds |
|---|---|---|
| `upload_drafts` (new columns) | job | `review_status` (`pending` / `approved` / `rejected`, default `pending`), `reviewed_by`, `reviewed_at`, `reject_reason`, `approved_version` (0 = the model's draft); one check keeps them matching the status |
| `draft_revisions` | edit | the job, `version` (1 and up, unique per job), the letter as JSON, its hash, `edited_by` |
| `audit_events` | action | the user, `action`, the job (empty exactly for `account_created`), `detail` as a JSON object |

As in 3.19, every link carries `account_id`: a revision links to its draft on (`account_id`,
`job_id`) (new key `uq_upload_drafts_account_job_id`) and is deleted with it; the links to
users and to jobs from `audit_events`, and to users from the review columns and revisions,
have no cascade. Migration 0017 set every existing draft to `pending` and every job's cost
to 0.

### Measured

The second "upload to draft ready" measurement (2026-10-17): `uvicorn` on the local machine,
`app` at migration 0017, one new made-up account, three made-up letters uploaded as text
files (agent `v5`).

| | Job 3 | Job 4 | Job 5 |
|---|---|---|---|
| Letter | `medical_necessity` | `medical_necessity` | `noncovered`, one of two lines denied |
| `202` to `done` | 18.6 s | 12.8 s | 10.2 s |
| End state | `draft_failed` | `draft_failed` | `needs_review` (drafted on attempt 1, one cited paragraph) |
| Drafting | 2 attempts, both `blocked` `quote_not_in_evidence` | the same | |
| `jobs.cost_usd` | $0.001817 | $0.001742 | $0.001028 |

**Upload to draft ready took 10.2 s, under the 120 s target, the first time with a draft.**
One of three uploads was drafted (33%). The three job costs add up to $0.004587, the same as
the 11 `llm_calls` rows of the run.

Through the API, on job 5: an edit of version 0 stored version 1 (a typo fixed, a sentence
reworded); a second edit of version 0 was `409`; text in square brackets was `422`
`placeholder_text`; a changed quote was `422` `edit_not_allowed`; approving version 0 was
`409`; approving version 1 was `200`; a second approve and an edit after it were `409`. The
model's draft stayed beside version 1. The other account got `404` for the job's detail and
its approve. `GET /audit` gave 6 events (account created, 3 uploads, edit, approve). The
server log held no error and no personal field.

### Known limits

- **`medical_necessity` uploads are not drafted yet.** Both ended `draft_failed`: all four
  attempts were blocked with `quote_not_in_evidence`, on the same five `cms_manual` passages.
  Some of those passages hold curly quotation marks, which the validator compares exactly.
  The trace keeps no model text, so the cause is not proven.
- **Three letters are too few** to say how often an upload is drafted.
- **A decision is final.** A wrongly approved or rejected draft cannot be reopened; the
  letter has to be uploaded again.
- **An edit cannot add evidence.** A citation can only be kept or removed; a new quote needs
  a new search and a new draft.
- **The citation check follows paragraph kinds, not what edited text says.** An edit keeps
  each paragraph's kind and citations but may change its text. A reviewer who writes a policy
  claim into a paragraph that is not `policy` passes both checks without a citation; a person
  wrote and approved that text.
- **A job's cost can differ from `llm_calls`.** A job marked `interrupted` at startup keeps
  `cost_usd` 0, even if it already paid for calls. The meter also counts a paid call whose
  `llm_calls` row could not be saved, so a job's cost can be higher than its recorded calls.
- **Approved means stored, not sent.** There is no export or letter file yet.
- **The audit log is only append-only in the code.** The database does not stop an `UPDATE`
  or `DELETE` on `audit_events`.
- **The cost of a month is by upload date.** A job uploaded just before midnight at the end
  of a month and finished after it counts in the month it was uploaded.
- **No dashboard yet.** Everything here works through the API only; the browser comes in the
  next Stage 5 branch.
