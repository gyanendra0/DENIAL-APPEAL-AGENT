"""Export a sample of drafted appeal letters for a person to score, then measure the share.

Usage:
    python3 -m pipelines.review_drafts export --out-dir <folder> [--size 30] [--seed 42]
        [--exclude-scored <score csv> ...]
    python3 -m pipelines.review_drafts score <path-to-review_scores.csv> [--size 30]

Run `pipelines.run_agent` first: a draft can be reviewed when the agent loop ended its claim
as `needs_review`, the draft is stored, and the denial letter document stored now is the one
the draft was built from.

`export` draws the sample (the same seed and the same stored drafts always give the same
sample) and writes two files into `--out-dir`: `review_drafts.md`, which shows the rubric
and, per draft, the draft, the denial letter document and every cited chunk in full; and
`review_scores.csv`, with one row per draft to fill in. The file to read holds document
text, so the folder must be one that is never committed, such as `data/review/`. An existing
`review_scores.csv` is never overwritten. `--exclude-scored`, given once per file, leaves out
every claim named in an earlier score file before the draw, so a new review reads only drafts
no earlier review read; a file whose verdicts are still empty can be used.

`score` reads the filled-in file, checks every row and prints the counts per verdict and per
reject reason, the acceptance share, and the share of the drafts with and without a citation.
The share is checked against the Stage 4 done condition, at least 60%. `--size` is the
number of drafts that were sampled; a file with another number of rows is refused.

Both steps only read the database. They make no model call and spend no money. The terminal
shows counts and shares only. The reviewer is a person: nothing here scores a draft. The
claims and documents are made up, so the share says nothing about real appeals.

Exit codes: 0 the files are written (`export`) or the share meets the target (`score`); 1
the share is below the target, the score file is not filled in or cannot be used, an
excluded score file cannot be used, the sample cannot be drawn or shown, or the score file
already exists; 2 bad arguments.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from src.agent.trace import AGENT_VERSION
from src.config.settings import get_settings
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.drafting.prompts import DRAFTING_PROMPT_VERSION
from src.evaluation.review_export import (
    REVIEW_FILE_NAME,
    SCORES_FILE_NAME,
    build_review_text,
    build_scores_csv,
)
from src.evaluation.review_sample import (
    REVIEW_SAMPLE_SEED,
    REVIEW_SAMPLE_SIZE,
    select_review_sample,
)
from src.evaluation.review_score import (
    ScoreFileError,
    build_review_report,
    check_review_scores,
    count_without_verdict,
    read_review_scores,
)
from src.evaluation.review_store import (
    ReviewDataError,
    read_review_drafts,
    read_review_items,
    select_reviewable_claims,
)

EXIT_OK = 0
EXIT_NOT_MET = 1
# How many problems of a score file are printed; the rest are counted.
MAX_PROBLEMS_SHOWN = 10


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return EXIT_NOT_MET


def _fail_with_problems(
    problems: Sequence[str], heading: str = "the score file cannot be used:"
) -> int:
    print(heading, file=sys.stderr)
    for problem in problems[:MAX_PROBLEMS_SHOWN]:
        print(f"  {problem}", file=sys.stderr)
    more = len(problems) - MAX_PROBLEMS_SHOWN
    if more > 0:
        print(f"  (and {more} more)", file=sys.stderr)
    return EXIT_NOT_MET


def _export(out_dir: Path, size: int, seed: int, excluded_paths: Sequence[Path]) -> int:
    scores_path = out_dir / SCORES_FILE_NAME
    if scores_path.exists():
        return _fail(
            f"{scores_path} already exists and is not overwritten: scores in it would be lost."
            " Give another --out-dir, or move the file away first"
        )
    excluded: set[str] = set()
    for path in excluded_paths:
        try:
            excluded.update(score.claim_id for score in read_review_scores(path))
        except ScoreFileError as exc:
            return _fail_with_problems(exc.problems, f"cannot use the excluded score file {path}:")
    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            reviewable = select_reviewable_claims(session, AGENT_VERSION, DRAFTING_PROMPT_VERSION)
            pool = [claim_id for claim_id in reviewable.claim_ids if claim_id not in excluded]
            try:
                sample = select_review_sample(pool, size=size, seed=seed)
                items = read_review_items(session, sample, DRAFTING_PROMPT_VERSION)
            except (ValueError, ReviewDataError) as exc:
                return _fail(f"cannot export the sample: {exc}")
    finally:
        engine.dispose()

    review_path = out_dir / REVIEW_FILE_NAME
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        # The file to read goes first: a failure here leaves no score file behind, so the
        # next export into this folder is not refused.
        review_path.write_text(build_review_text(items), encoding="utf-8")
        # "x": the score file is created, never replaced.
        with scores_path.open("x", encoding="utf-8", newline="") as file:
            file.write(build_scores_csv(items))
    except FileExistsError:
        return _fail(f"{scores_path} already exists and is not overwritten")
    except OSError as exc:
        return _fail(f"cannot write the files into {out_dir} ({type(exc).__name__})")

    cited = sum(bool(item.evidence) for item in items)
    print(
        f"exported {len(items)} of {len(pool)} drafts that can be reviewed"
        f" (seed {seed}, agent {AGENT_VERSION}, drafting prompt {DRAFTING_PROMPT_VERSION})"
    )
    print(f"  with at least one citation: {cited}")
    print(f"  left out, the denial letter changed after the draft: {reviewable.out_of_date}")
    print(f"  left out, named in an earlier score file: {len(reviewable.claim_ids) - len(pool)}")
    print(f"  file to read: {review_path}")
    print(f"  file to fill in: {scores_path}")
    return EXIT_OK


def _score(path: Path, size: int) -> int:
    try:
        scores = read_review_scores(path)
    except ScoreFileError as exc:
        return _fail_with_problems(exc.problems)
    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            reviewable = select_reviewable_claims(session, AGENT_VERSION, DRAFTING_PROMPT_VERSION)
            try:
                drafts = read_review_drafts(session, reviewable.claim_ids, DRAFTING_PROMPT_VERSION)
            except ReviewDataError as exc:
                return _fail(f"cannot read the stored drafts: {exc}")
    finally:
        engine.dispose()

    problems = check_review_scores(
        scores,
        {claim_id: draft.draft_sha256 for claim_id, draft in drafts.items()},
        size=size,
    )
    if problems:
        return _fail_with_problems(problems)
    open_rows = count_without_verdict(scores)
    if open_rows:
        return _fail(
            f"{open_rows} of {len(scores)} drafts have no verdict yet: fill in the score file,"
            " then run this step again. No share is worked out before that"
        )
    report = build_review_report(
        scores, {claim_id: draft.has_citation for claim_id, draft in drafts.items()}
    )
    print(report.as_text())
    return EXIT_OK if report.target_met else EXIT_NOT_MET


def main(argv: Sequence[str] | None = None) -> int:
    """Run one step of the draft review. Reads the database only; takes a few seconds."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    steps = parser.add_subparsers(dest="step", required=True)
    export = steps.add_parser("export", help="write the file to read and the score file")
    export.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="the folder the two files are written into (never a committed one)",
    )
    export.add_argument(
        "--size",
        type=_positive_int,
        default=REVIEW_SAMPLE_SIZE,
        help=f"how many drafts are sampled (default {REVIEW_SAMPLE_SIZE})",
    )
    export.add_argument(
        "--seed",
        type=int,
        default=REVIEW_SAMPLE_SEED,
        help=f"the seed of the draw (default {REVIEW_SAMPLE_SEED})",
    )
    export.add_argument(
        "--exclude-scored",
        type=Path,
        action="append",
        default=[],
        metavar="SCORE_CSV",
        help="leave out every claim named in this earlier score file (may be given again)",
    )
    score = steps.add_parser("score", help="check the filled-in score file and print the share")
    score.add_argument("path", type=Path, help="the filled-in score file (CSV)")
    score.add_argument(
        "--size",
        type=_positive_int,
        default=REVIEW_SAMPLE_SIZE,
        help=f"how many drafts were sampled (default {REVIEW_SAMPLE_SIZE})",
    )
    args = parser.parse_args(argv)
    if args.step == "export" and args.out_dir.exists() and not args.out_dir.is_dir():
        parser.error(f"not a folder: {args.out_dir}")
    if args.step == "score" and not args.path.is_file():
        parser.error(f"file not found: {args.path}")

    logging.basicConfig(level=get_settings().log_level)
    if args.step == "export":
        return _export(args.out_dir, args.size, args.seed, args.exclude_scored)
    return _score(args.path, args.size)


if __name__ == "__main__":
    raise SystemExit(main())
