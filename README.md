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
| - | [Learning Notes](docs/learning.md) | Why every decision was made, and what we rejected |

## Status

**Design complete, implementation starting at Stage 0.** See the
[build plan](docs/05-build-plan.md).

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
