# Learning Notes

Why this project is set up the way it is. Every decision, in simple words: what we chose,
what else we could have chosen, and why we went this way.

Read this when you have forgotten why something is the way it is, or when you want to
argue with a past decision.

---

## Part A — Why use Git at all

**Git is a time machine plus a safety net.**

Without it you get folders named `project-final`, `project-final-2`,
`project-final-ACTUAL`. You cannot tell what changed between them, and you cannot go back
to a working version once you have broken something.

With Git you get:

- Every version of every file, forever.
- The ability to try something risky and throw it away with one command.
- A written record of *why* each change was made.

**Why GitHub on top of Git?** Git lives on your machine only. If the laptop dies, the
project dies. GitHub is an off-site copy, plus the place where pull requests, issues and
automated checks live.

---

## Part B — Repository setup decisions

### B1. One branch called `main`, nothing else permanent

**What we chose:** a single permanent branch, `main`. Feature branches are temporary and
deleted after merging.

**What else exists:** "Git Flow" — permanent `develop`, `release/*` and `hotfix/*`
branches alongside `main`.

**Why we did not use Git Flow:** it was designed for teams shipping versioned software on
a release schedule, with several people working in parallel. One person shipping
continuously gets all the overhead and none of the benefit. You would spend time keeping
`develop` and `main` in sync for no reason.

**Rule of thumb:** the number of permanent branches should match the number of things you
genuinely release separately. For us that is one.

### B2. Squash merge only

**What we chose:** when a pull request merges, all its commits get flattened into one
commit on `main`.

**What else exists:**

| Style | What it does | Problem for us |
|---|---|---|
| Merge commit | Keeps every branch commit plus a "merge" commit | `main` fills with `wip`, `fix typo`, `try again` |
| Rebase merge | Replays every branch commit onto `main` | Same noise, no merge commit |
| **Squash** | One commit per feature | — |

**Why squash wins here:** `main` becomes a list of *features*, not a list of *keystrokes*.
Six months later, `git log` reads like a changelog instead of a diary.

**The bonus effect:** because your branch commits get flattened anyway, you are free to
commit messily and often while working. That is a good habit — frequent commits mean you
never lose more than a few minutes of work.

### B3. Linear history

**What we chose:** required linear history, which forces rebase instead of merge when
catching up with `main`.

**Simple explanation:** history becomes a straight line instead of a braided rope. To find
when a bug appeared, you can walk backwards one commit at a time. With a braided history
you have to reason about which branch each commit came from.

### B4. Auto-delete branches after merge

**Why:** branches are disposable. Once merged, the branch has served its purpose and its
commits live on `main`. Keeping them gives you a list of forty dead branches and no way to
tell which are still relevant.

### B5. Public repository

**What we chose:** make the repository public.

**Why:**

1. **The protections only work if you do.** Branch rulesets and required status checks are
   restricted on free *private* repositories. Public gets them for free.
2. **There is nothing to hide.** All our data is public. Secrets live in the environment,
   never in the repo.
3. **It is a portfolio piece.** A well-run public repository with clean history and real
   documentation is worth showing.

**The trade-off:** anyone can read it. That is fine here — but it means the "no secrets"
rule stops being a nicety and becomes critical. A leaked key in a public repo is scanned
by bots within minutes.

---

## Part C — Protecting `main`

### C1. Why block direct commits to `main`

**The failure it prevents:** it is late, you make "one tiny change", commit straight to
`main`, and push. It is broken. Now the one branch that is supposed to always work does
not, and anything you build next sits on top of a broken base.

**The rule forces a pause.** Making a branch takes three seconds and creates a space where
being wrong is free.

**But you are alone — who are you protecting `main` from?** From yourself, an hour from
now, tired. Process is how you outsource discipline to a machine so you do not have to
supply it at 11pm.

### C2. Why zero required approvals

**The problem:** GitHub will not let you approve your own pull request. It is deliberate —
approval is meant to be a second pair of eyes.

**What happens if you set approvals to 1 anyway:** nothing can ever merge. You lock
yourself out of your own project, and the only way forward is to disable the rule you just
made. A rule you have to break is worse than no rule.

**What we did instead:** approvals set to 0, and **CI becomes the reviewer**. The required
status check is the real gate. It cannot be sweet-talked, and it never gets tired.

**What this means in practice:** the automated checks matter far more on a solo project
than on a team. On a team, a human might catch what CI misses. Here, CI is all there is —
so it must be strict.

### C3. Why block force pushes

**What a force push does:** overwrites remote history with your local version. Commits
that existed on the remote simply vanish.

**Why block it on `main`:** it is the one operation in Git that genuinely destroys work.
Everything else can be recovered.

**Why still allow it on feature branches:** after a rebase, your branch's history has been
rewritten, and a normal push will be rejected. Force pushing is the correct move — and
because the branch is only yours, nobody else loses anything.

**Always use `--force-with-lease`, never `--force`.** `--force-with-lease` first checks
whether the remote has commits you have not seen, and refuses if so. Plain `--force` does
not check and will happily destroy them.

---

## Part D — Daily workflow decisions

### D1. Why branch names have a type prefix

`feat/`, `fix/`, `chore/`, `docs/`…

**Why:** you can tell what a branch is for without opening it. It also forces a small
useful question before starting: *is this a feature, or a fix, or just tooling?* If you
cannot answer, the work is not well defined yet.

### D2. Why one branch per feature group

**What we chose:** related tasks share one branch — for example `feat/database-foundation`
holds the settings module, the database session and the first migration together.

**What else exists:**

| Approach | Problem |
|---|---|
| One branch per tiny task | Many branches and PRs for pieces that are useless alone. Pure overhead for one person |
| One branch per whole stage | PRs of 2,000+ lines nobody can review, and CI feedback arrives far too late |
| **One branch per feature group** | — |

**Why the middle wins:** few branches to manage, but each PR is still small enough to read
properly and to revert cleanly if it turns out wrong.

**The failure it still prevents:** mixing *unrelated* work. A data loader and a README typo
fix do not belong on one branch — if one is wrong, you cannot revert it without losing the
other.

**Simple test:** would these tasks make sense merged separately? If not, they are one
group. If yes, and they are unrelated, they are two branches.

### D3. Why pull requests stay under ~400 lines

**Because attention does not scale.** Reviewing 50 lines, you read every one. At 400 you
skim. At 1,000 you scroll to the bottom and approve. The limit is not about tidiness — it
is the point past which review stops being real.

**This applies even reviewing your own work.** Reading your own diff is how you catch the
debug print you left in.

### D4. Why read your own diff before merging

**The single highest-value habit in this whole document.**

Writing code and reviewing code are different mental modes. Writing, you are thinking
"make it work". Reviewing, you are thinking "is this right?" Switching modes catches:
leftover `print()` calls, commented-out experiments, a hard-coded path, a `TODO` you meant
to finish, a file you did not mean to add.

It costs two minutes and catches something roughly one time in three.

### D5. Why commit messages have a format

```text
feat: extract denial reason codes from letter text
```

**Why not just "updates" or "stuff":** in six months you will be reading `git log` trying
to find when a behaviour changed. "stuff" tells you nothing. The format makes the history
searchable — `git log --oneline | grep "^fix"` shows every bug fix ever made.

**Why imperative mood** ("add" not "added"): Git's own generated messages use it, so your
history stays consistent. Minor, but free.

---

## Part E — What we do and do not commit

### E1. Why data is never committed

**Practical reason:** Git stores the full content of every version of every file forever.
Commit a 200 MB dataset, change it three times, and the repository is 800 MB permanently —
even after deleting the file, because it lives in the history.

**Correctness reason:** data should be *reproducible*, not *stored*. If a script can
download and rebuild it, the script is the source of truth. If you store the data instead,
nobody knows which version is current or how it was made.

### E2. Why models are never committed

Same size problem, plus: a model is an *output*. It is produced by code plus data plus a
random seed. Store those three and the model can be recreated. Store the model alone and
you have a binary blob nobody can explain.

### E3. Why `node_modules/` and `.venv/` are never committed

They are downloaded, not written. The dependency list (`package.json`,
`pyproject.toml`) is the real source of truth — from it, anyone can rebuild the exact same
folders. Committing them adds tens of thousands of files you did not write.

### E4. Why working docs are not committed

**This is the rule you asked for, and it is a good one.**

Two kinds of document:

| Kind | Example | Lifespan |
|---|---|---|
| **Published** | "Here is how the data pipeline works" | Years |
| **Working** | "Tuesday: finish the loader, then test it" | Days |

**Why keep working docs out:**

1. **They go stale instantly.** A repo with eight task sheets from different weeks — which
   one is current? Nobody knows, so all of them get ignored.
2. **They drown the history.** Task sheets change hourly. In `git log` they would bury the
   actual code changes.
3. **They duplicate the project board,** which already tracks work and does it better.
4. **Private notes stay honest.** "This approach is probably wrong but let us try it" is a
   useful note to yourself and an awkward thing to publish. Keeping the space private
   keeps it truthful.

**The important part:** when a working note contains a real *decision* — we chose this
data source, we rejected that approach because X — **move the decision into a published
doc**. The decision survives; the scratch does not.

### E5. Why `.gitattributes` exists

**The problem it solves:** Windows ends lines with two invisible characters, Linux and Mac
with one. Without `.gitattributes`, moving between machines makes Git think every line of
every file changed. Diffs become useless.

`* text=auto eol=lf` tells Git to normalise everything to one style on the way in. You
never think about it again.

---

## Part F — Technology choices

### F1. PostgreSQL *and* pgvector, not a separate vector database

**What we need:** ordinary tables (claims, users, drafts) **and** vector search for
finding relevant policy text.

**The obvious approach:** PostgreSQL for tables, plus Pinecone or Weaviate or Qdrant for
vectors.

**Why we did not:**

| Cost | Two systems | One system |
|---|---|---|
| Money | A second service to pay for | Free |
| Complexity | Two connections, two backups, two things to break | One |
| Consistency | A row and its vector can drift out of sync | One transaction, always consistent |

**pgvector** is an extension that teaches PostgreSQL to store and search vectors. It is
slower than a dedicated vector database at very large scale — but "very large scale" here
means millions of documents, and we will have thousands.

**The lesson:** pick the boring option that removes a moving part, until you can prove you
need the exciting one.

### F2. A database table as the job queue, not Redis or Celery

**What we need:** upload a document, then do slow work (extract, score, draft) in the
background.

**The standard answer:** Celery with Redis or RabbitMQ.

**Why not:** that is another service to run, another thing to monitor, another line on the
bill. For our volume — a handful of jobs at a time — a table with a `status` column does
the same job.

**When this becomes wrong:** thousands of jobs per minute, or you need complex retry and
scheduling. Then switch. Not before.

### F3. Gradient boosting before neural networks

**The task:** given facts about a denial (amount, payer, reason code, timing), predict
whether an appeal wins.

**Why not deep learning:** this is **tabular** data — rows and columns, not images or raw
text. On tabular data, gradient-boosted trees (XGBoost, LightGBM) usually match or beat
neural networks, while training in seconds on a laptop, needing far less data, and being
much easier to explain.

**Explainability matters here.** The dashboard must show *why* a denial scored 0.8. Trees
give that directly. A neural network needs extra machinery to approximate it.

**The lesson:** "AI project" does not mean "use the most complicated model". Use the model
that fits the shape of the data.

### F4. A hosted LLM with an open-weight fallback

**Why a hosted API first:** quality. The best models are hosted, and running a good
open-weight model needs a GPU we are not paying for.

**Why a fallback at all:**

1. **Cost control.** If the month's budget is spent, fall back rather than stop.
2. **Not being trapped.** One provider, one price rise, one policy change, and a
   single-provider project is stuck.

**Why this forces a good design:** because two providers must be swappable, all model calls
go through one gateway module. That gateway becomes the natural place for the budget check,
caching, retries and logging — things that would otherwise be scattered everywhere.

**The lesson:** designing for a second option usually improves the structure even if you
never use the second option.

### F5. FastAPI

**Why:** it reads types off your function signatures and uses them to validate incoming
data and generate API documentation automatically. You write the types once — which you
should do anyway — and get validation and docs for free.

**The alternative, Django,** brings an admin panel, its own ORM and a lot of structure.
Excellent for a content-driven site; more than we need for an API with a separate React
frontend.

### F6. React with Vite

**Why Vite over Create React App:** CRA is effectively unmaintained, and its rebuilds get
slow as a project grows. Vite rebuilds in milliseconds. On a small project this is the
difference between enjoying the frontend work and avoiding it.

### F7. The $5/month constraint

**Why deliberately cap the budget:** a constraint is a design tool. "Unlimited budget"
quietly leads to a managed database, a managed queue, a managed vector store, a managed
model endpoint — a system that works but costs $200/month and cannot be explained.

Forcing everything onto one small machine with one database produced a *simpler*
architecture, not just a cheaper one.

**What it cost us:** no autoscaling, no high availability, a single point of failure. For a
project with a handful of users, that is the right trade. For real hospital traffic it
would not be.

---

## Part G — Product decisions

### G1. Why a human must approve every letter

**Not primarily a technology decision.** An appeal letter is a formal document sent to an
insurer about someone's medical care. Sending one automatically, based on a model's
guess, is not acceptable regardless of how good the model is.

**The practical effect:** it changes what "good" means. The system does not need to be
right every time — it needs to be **useful and honest**. A draft that saves twenty minutes
of writing is valuable even when it needs editing. So the design shows confidence scores
and evidence rather than hiding them behind a single answer.

### G2. Why every policy claim must cite retrieved evidence

**The failure this prevents:** a language model will produce a confident, well-written,
completely invented policy reference. In an appeal letter that is worse than useless — it
damages credibility with the insurer.

**The rule:** if the letter states a policy fact, it must link to a chunk of text that was
actually retrieved. No matching chunk, no claim. The draft is **blocked**, not warned
about.

**Why block rather than warn:** warnings get ignored. Every time. If the rule matters,
enforce it.

### G3. Why `account_id` on every table

**What it prevents:** one customer seeing another customer's data. The single worst bug
this kind of system can have.

**Why a column rather than separate databases per customer:** separate databases are safer
but multiply the operational work — migrations, backups and connections per customer. A
column plus a strict filtering rule is the standard approach at our scale.

**Why it is called out as a hard rule:** because it relies on discipline. One query that
forgets the filter and the isolation is gone. That is exactly the kind of rule that
belongs in writing and in a test.

---

## Part H — Decisions we reversed, and why

Being wrong and changing course is normal. Recording it stops you re-litigating the same
argument later.

| We first said | We now say | Why it changed |
|---|---|---|
| Two developers, split work | One person, end to end | The second developer did not materialise. Splitting work for an imaginary teammate is pure overhead. |
| Deploy on AKS or AWS | One small always-on machine | AKS costs money continuously. The free tier of a small VM covers our load entirely. |
| Use AWS Bedrock | A hosted API with an open-weight fallback | Bedrock adds an AWS account, IAM setup and its own pricing model to solve a problem a plain API key already solves. |
| Separate vector database | pgvector inside PostgreSQL | One fewer service, one fewer bill, no sync problem. |
| Docker Compose for deployment | Containers on one machine | Compose is a local-development tool. It has no restart, health or rollout story for a long-running deployment. |
| An identity provider for login | Plain accounts with roles | An IDP solves single sign-on across many applications. We have one application. |
| Weekly task sheets in the repo | Task sheets stay local | They went stale within days and buried the real history. |
| Required 1 approval on pull requests | 0 approvals, CI is the gate | GitHub forbids self-approval — the rule would have blocked every merge. |
| One branch per small task | One branch per feature group | Too many branches for one person. Grouping related tasks keeps branches few while PRs stay reviewable. |

---

## Part I — The ideas worth keeping

If you forget everything else:

1. **Prefer the boring option.** Fewer moving parts beats clever, almost always.
2. **A constraint is a design tool.** The $5 limit made the architecture simpler, not just
   cheaper.
3. **Automate the discipline you cannot rely on.** You will be tired. CI will not be.
4. **Make the right thing the easy thing.** Task sheets live in an ignored folder, so the
   rule enforces itself.
5. **Store the recipe, not the cake.** Commit the code that makes the data and the model,
   never the data or the model.
6. **Write decisions down when they are made.** Not later. Later never comes, and the
   reasoning is what disappears first.
7. **Block, do not warn.** A warning everyone ignores is not a safeguard.
8. **Match the model to the shape of the data.** Tabular data wants trees, not a neural
   network.
9. **Read your own diff.** Two minutes, and it catches the thing you would otherwise ship.
10. **A rule you have to break is worse than no rule.** Design rules you can actually keep.
