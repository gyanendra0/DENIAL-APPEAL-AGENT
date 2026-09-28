# Repository Rules

The complete operating manual for this repository: how it is created, how work enters it,
and what is not allowed. Read top to bottom once, then use it as a reference.

**Built by one person.** The rules still apply — they exist so the history stays readable,
mistakes get caught by machines instead of by memory, and the project can be picked up
again after a month away.

> **Where these commands run.** Code and documentation are written on the authoring
> machine. Everything in this document — `git init`, commits, pushes, GitHub settings, CI,
> deployment — happens on the **run machine**. Files are carried across; commands are not
> run where they are written.

---

## Part 1 — Creating the repository

### 1.1 Local setup

Run once, in the project folder:

```powershell
git init -b main
git add .
git commit -m "docs: project idea and design"
```

`main` is the only permanent branch. There is no `develop`, no `release` branch. Keeping a
single trunk is what makes the rest of this document simple.

### 1.2 Remote setup

Create an **empty** repository on GitHub — no README, no licence, no `.gitignore`, because
those already exist locally and would cause a conflict on the first push.

```powershell
git remote add origin https://github.com/<user>/denial-appeal-agent.git
git push -u origin main
```

### 1.3 Repository settings

In **Settings → General**:

| Setting | Value | Why |
|---|---|---|
| Default branch | `main` | Single trunk |
| Allow merge commits | **Off** | Keeps history linear |
| Allow squash merging | **On** | One commit per feature on `main` |
| Allow rebase merging | Off | One merge style only, no decisions to make |
| Automatically delete head branches | **On** | Branch list stays short without effort |
| Always suggest updating pull request branches | On | Catches drift from `main` early |

---

## Part 2 — Protecting `main`

**Rule 1: never commit directly to `main`.** Everything arrives through a pull request.

### 2.1 Create the ruleset

**Settings → Rules → Rulesets → New branch ruleset**

| Field | Value |
|---|---|
| Name | `protect-main` |
| Enforcement status | **Active** |
| Target branches | Include default branch |

Enable these rules:

- **Restrict deletions** — `main` cannot be deleted.
- **Block force pushes** — history cannot be rewritten.
- **Require a pull request before merging**
  - Required approvals: **0** (see the note below)
  - Dismiss stale approvals when new commits are pushed: on
- **Require status checks to pass**
  - Require branches to be up to date before merging: on
  - Add the `checks` job once it has run at least once — GitHub cannot offer the name
    until it has seen it
- **Require linear history**

### 2.2 The self-approval problem

**GitHub does not let you approve your own pull request.** If required approvals is set to
1 on a solo repository, nothing will ever merge.

So: required approvals is **0**, and the real gate is the **status check**. CI becomes the
reviewer. This is why Part 4 matters more here than on a team project — the automated
checks are the only thing standing between a mistake and `main`.

> On a **free private** repository, rulesets and branch protection are restricted. Either
> make the repository **public**, or accept that protection is advisory and enforce the
> rules by discipline. Public is recommended: the data is public, there is no secret to
> protect, and the protections become real.

### 2.3 What protection gives you

| Without | With |
|---|---|
| A tired late-night commit lands on `main` | It lands on a branch and CI fails first |
| A force push loses work | Force push is refused |
| Broken code sits on `main` for days | Broken code never reaches `main` |

---

## Part 3 — Branches

### 3.1 Naming

```text
<type>/<short-description>
```

Lower case, hyphen separated, no spaces, no issue numbers in the name.

| Type | Use for | Example |
|---|---|---|
| `feat` | New behaviour | `feat/denial-extraction` |
| `fix` | Broken behaviour | `fix/upload-size-limit` |
| `chore` | Tooling, dependencies, config | `chore/add-ruff` |
| `docs` | Documentation only | `docs/data-sources` |
| `refactor` | Restructure, no behaviour change | `refactor/split-agent-loop` |
| `test` | Tests only | `test/extraction-edge-cases` |
| `perf` | Speed or cost improvement | `perf/cache-embeddings` |

**Rule 2: one branch per feature group, and only one branch open at a time.**

A **feature group** is a set of related tasks that only make sense together — for example
the settings module, the database session and the first migration are all "database
foundation". Group them on one branch instead of opening a branch per task.

| Too small (avoid) | Right size | Too big (avoid) |
|---|---|---|
| `chore/add-settings`, `chore/add-session`, `feat/add-migration` — three branches | `feat/database-foundation` — one branch | `stage-0/everything` — the whole stage |

How to decide whether tasks belong together:

- **Together:** they touch the same component, or one is useless without the other.
- **Apart:** they are unrelated — a data loader and a README typo are two branches.
- **Split it:** the pull request would pass roughly 400 changed lines.

Aim for **2–4 branches per stage**. Open the next branch only after the current one is
merged.

### 3.2 Lifetime

A branch should live **a few days at most**. Long branches drift from `main`, collect
conflicts, and produce pull requests too large to review honestly.

If a feature group grows too large, merge what is finished and continue the rest on a new
branch.

---

## Part 4 — Automated checks

CI is the reviewer. It must be fast enough that you actually wait for it.

### 4.1 What runs on every pull request

| Step | Tool | Fails when |
|---|---|---|
| Lint | `ruff` | Style or obvious bug patterns |
| Format | `black --check` | Formatting differs |
| Types | `mypy` | Type errors |
| Tests | `pytest` | Any test fails |
| Coverage | `pytest --cov` | Coverage on `src/` below 70% |
| Secrets | secret scan | A credential appears in the diff |

### 4.2 Rules

- **Rule 3: never merge on red.** No exceptions, no "it is only a docs change". If CI is
  wrong, fix CI.
- The whole suite targets **under five minutes**. Beyond that it gets skipped mentally.
- Tests never call a real API, never download from the internet, never touch a real
  database outside the throwaway test one.

---

## Part 5 — Commits

### 5.1 Format

```text
<type>: <what changed, imperative, lower case, no full stop>
```

Same type list as branches.

```text
feat: extract denial reason codes from letter text
fix: reject uploads over the size limit
docs: record the chosen claims data source
chore: pin sqlalchemy to 2.0
```

Use the body only when the *why* is not obvious from the change:

```text
perf: cache embeddings by content hash

Re-embedding the same policy text on every run was the largest single
line on the monthly bill. The hash is stable across runs, so a cache
hit is safe.
```

### 5.2 Rules

- **Rule 4: commit small and often on your branch.** Squash merge collapses them, so a
  messy branch history costs nothing and protects work in progress.
- Never commit a file you have not looked at.
- Never commit generated files, data, models or secrets — `.gitignore` covers these, but
  check the diff anyway.
- **Rule 5: if `git status` shows something you cannot explain, stop and find out why.**

---

## Part 6 — The pull request cycle

### 6.1 The loop

```text
1.  git checkout main
2.  git pull
3.  git checkout -b feat/thing

4.  ... write code and its tests together ...
5.  run the checks locally
6.  git add -p          (review every hunk as you stage it)
7.  git commit
8.  git push -u origin feat/thing

9.  open the pull request
10. read your own diff, top to bottom
11. wait for CI
12. squash merge
13. branch auto-deletes
14. git checkout main && git pull
```

Step 10 is not optional and it is not a formality. Reading your own diff as though someone
else wrote it catches debug prints, commented-out code, a stray `TODO`, a hard-coded path,
and the thing you meant to come back to.

### 6.2 Pull request description

```markdown
## What
One or two sentences.

## Why
Closes #12

## How to verify
1. ...
2. ...

## Checklist
- [ ] Tests written alongside the code
- [ ] Published docs updated in this same pull request
- [ ] No task sheets, scratch notes or drafts in the diff
- [ ] No secrets, data files or models in the diff
- [ ] Stays within the monthly budget
```

### 6.3 Size

**Rule 6: keep pull requests under roughly 400 changed lines.** Past that, real review
stops and rubber-stamping begins. If it is bigger, it was two pull requests.

Generated files and lock files do not count toward the limit.

### 6.4 Merge style

**Squash merge, always.** One feature becomes one commit on `main`. Reading `main`'s
history then tells the story of the project rather than the story of your afternoon.

Edit the squash commit message before confirming — GitHub pre-fills it with every branch
commit, which is noise.

---

## Part 7 — Keeping a branch current

When `main` has moved ahead:

```powershell
git checkout main
git pull
git checkout feat/thing
git rebase main
```

Rebase, not merge, so history stays linear. This is safe **because the branch is yours
alone** — never rebase a branch someone else has pulled.

On a conflict: fix the files, then

```powershell
git add <files>
git rebase --continue
```

To abandon the attempt and return to where you started:

```powershell
git rebase --abort
```

Then force-push the rebased branch — acceptable on a feature branch, never on `main`:

```powershell
git push --force-with-lease
```

`--force-with-lease` refuses if the remote has commits you have not seen. Use it instead
of `--force`, always.

---

## Part 8 — Issues and tracking

### 8.1 Issues

Every branch starts as an issue — **one issue per feature group**, not one per small task.
List the tasks inside the issue as a checklist. Title carries the stage:

```text
[S0] Database foundation
[S1] Claims data loader
[S3] Schema-bound extraction
```

An issue body states what, why, the task checklist, and how you will know it is done.

### 8.2 Board

One project board, five columns:

```text
Backlog  →  This week  →  In progress  →  In review  →  Done
```

**Rule 7: one issue in progress at a time.** Finish it, or explicitly move it back to
*This week*. Parallel half-finished work is how solo projects stall.

### 8.3 Linking

Put `Closes #12` in the pull request body. The issue closes itself on merge and the two
stay connected in the history.

---

## Part 9 — Releases

Tag when a stage completes:

```powershell
git tag -a v0.1.0 -m "Stage 0: foundation"
git push origin v0.1.0
```

| Part | Meaning |
|---|---|
| Major | Breaking change to the API or data model |
| Minor | A stage completed, new capability |
| Patch | Fixes only |

Write release notes at the tag. It takes two minutes and it is the only record of what a
version actually contained.

---

## Part 10 — Which documents get pushed

**Rule 9: only published documents are committed. Working documents stay local.**

### 10.1 The two kinds

| Kind | What it is | Committed? |
|---|---|---|
| **Published** | Design and reference. Describes what the system *is* | **Yes** |
| **Working** | Task sheets, checklists, scratch notes. Describes what *you are doing this week* | **No** |

The test: *would this still make sense to someone reading the repository in six months?*
A description of the data strategy would. A checklist of what to do on Tuesday would not.

### 10.2 Published — committed

```text
README.md
docs/01-problem-and-scope.md
docs/02-data.md
docs/03-architecture.md
docs/04-product-and-dashboard.md
docs/repo-rules.md
```

These change through a pull request like any code change, and the pull request that alters
behaviour updates them in the same change.

### 10.3 Working — never committed

```text
docs/working/          everything in here
docs/**/week-*.md      weekly task sheets
docs/**/*-tasks.md     stage task sheets
docs/**/*-notes.md     working notes
docs/**/*.draft.md     drafts not ready to publish
TODO.md
NOTES.md
scratch/
```

Already covered by `.gitignore`. Keep task sheets in `docs/working/` and they are ignored
automatically, without thinking about it.

### 10.4 Why separate them

- A repository full of stale checklists is noise. Nobody knows which are current.
- Task sheets change hourly. They would dominate the history and hide real changes.
- Planning notes are a private thinking space. Committing them makes them formal, and
  formal notes stop being honest.
- The project board already tracks work. A checklist in Git duplicates it and drifts.

### 10.5 When a working doc becomes published

If a working note turns out to contain a real decision — a chosen data source, a rejected
approach and the reason — **move that decision into the relevant published doc** and let
the note stay local. The decision survives; the scratch does not.

---

## Part 11 — Security rules
**Rule 10: no secret ever enters the repository.** Not in code, not in a test, not in a
notebook, not in a commit message, not in a prompt.

- Configuration comes from the environment. `.env.example` lists key names with
  placeholder values only.
- If a secret is committed, **rotate it immediately** — deleting the commit is not enough,
  it is in the history and possibly in someone's cache.
- Enable secret scanning and push protection in **Settings → Code security**.
- Enable Dependabot alerts. Review them; do not auto-merge them blindly.

---

## Part 12 — Definition of done

A change is done when **all** of these hold:

1. Code merged to `main` through a pull request.
2. Tests exist for the new behaviour and pass.
3. Lint, format and type checks pass.
4. **Published** documentation affected by the change was updated **in the same pull
   request**.
5. It can be demonstrated — a command, a screen, or a report.
6. It stays inside the budget.
7. The branch is deleted.

Documentation updated *later* means documentation never updated. Same pull request, every
time.

---

## Part 13 — The rules, in one place

| # | Rule |
|---|---|
| 1 | Never commit directly to `main` |
| 2 | One branch per feature group, one branch open at a time |
| 3 | Never merge on red CI |
| 4 | Commit small and often on your branch |
| 5 | If `git status` surprises you, stop and investigate |
| 6 | Pull requests under ~400 changed lines |
| 7 | One issue in progress at a time |
| 8 | Squash merge, linear history, delete the branch |
| 9 | Only published docs are committed — task sheets stay local |
| 10 | No secret ever enters the repository |
| 11 | Published docs change in the same pull request as the code |

---

## Part 14 — Setup checklist

Work through once, in order:

- [ ] `git init -b main`, first commit
- [ ] Empty remote repository created, `main` pushed
- [ ] Repository made public, or protection limits accepted
- [ ] Merge commits off, squash on, auto-delete branches on
- [ ] Ruleset `protect-main` active: no deletion, no force push, pull request required,
      linear history
- [ ] CI workflow added and run once on a pull request
- [ ] `checks` added as a required status check
- [ ] Pull request template added at `.github/pull_request_template.md`
- [ ] Secret scanning, push protection and Dependabot enabled
- [ ] Project board created with the five columns
- [ ] `docs/working/` created for task sheets, confirmed ignored by `git status`
- [ ] First issue written and moved to *In progress*

---

## Part 15 — Recovering from mistakes

| Situation | Fix |
|---|---|
| Committed to `main` locally, not pushed | `git reset --soft HEAD~1`, branch, commit there |
| Wrong commit message, not pushed | `git commit --amend` |
| Committed a secret | Rotate the secret first, then clean the history |
| Committed a large data file | Remove it, add it to `.gitignore`, rewrite the branch |
| Branch is a mess | `git rebase -i main` and squash it into sense |
| Merged something broken | Revert the merge commit, then fix forward on a new branch |
| Lost a commit | `git reflog` — it is almost certainly still there |

`git reflog` is the safety net. Almost nothing in Git is truly lost for about ninety days.
