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
| `ml` | Predict appeal success (see 3.10) | Gradient boosting first; a neural net only if it beats it |
| `rules` | Hard policy gates: deadlines, amount floors, must-review cases (see 3.11) | Deterministic, testable, no LLM |
| `rag` | Find and rank supporting evidence | pgvector, chunked policy corpus |
| `agent` | Run the loop, call tools, stop and ask a human | Bounded steps, full trace logged |
| `llm` | One gateway for all model calls (see 3.8) | Primary API, open-weight fallback, budget guard |
| `api` | HTTP surface, auth, per-account isolation | FastAPI, JWT, role checks |
| `db` | Schema and migrations | SQLAlchemy + Alembic |
| `synth` | Generate Layer C documents | Seeded and reproducible |
| `evaluation` | Measure extraction, model and rules against the stage targets (see 3.12) | Read-only, no LLM, nothing stored |

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

The business table `predictions` does not exist yet either. The win-probability model is
trained and saved as files, and no score is stored (see 3.10).

A rule verdict is not stored either. The rules are a function that is called when a verdict
is needed (see 3.11); the first place a verdict will be written down is the agent's trace.

An evaluation result is not stored either. The evaluation harness prints its report and
writes nothing (see 3.12).

## 3.5 Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Ecosystem for ML and LLMs |
| API | FastAPI + Pydantic | Typed, fast, auto docs |
| ORM / migrations | SQLAlchemy 2 + Alembic | Standard, testable |
| Database | PostgreSQL + pgvector | One store for rows *and* vectors — keeps cost near zero |
| Jobs | Postgres-backed queue | No extra broker to pay for or run |
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
│  ├─ evaluation/   measured results against the stage targets
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
business table `predictions` (3.4) is built when customer uploads exist.

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
- A customer's denial cannot be scored yet: nothing calls `load_win_model` outside the
  tests, and no score is stored.

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
- Only the evaluation harness (3.12) calls `evaluate_rules` outside the tests. The agent
  loop will.

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
