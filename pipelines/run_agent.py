"""Run the agent loop for the denied claims of one dataset split and store each claim's trace.

Usage:
    python3 -m pipelines.run_agent --split test --rules-today 2010-01-01 [--limit 50]
        [--prompt-version v1]

Run `pipelines.extract_documents`, `pipelines.train_win_model` and
`pipelines.embed_evidence_corpus` first. Per claim the loop reads the stored extraction of the
denial letter, runs the rules, scores the claim with the saved model in the folder named by
the `MODEL_DIR` setting, searches the embedded evidence and drafts a cited appeal letter. The
order is fixed; no model picks a step.
THIS SPENDS MONEY: per claim that passes the rules and the score threshold, one small
embedding call for the search question and one model call for the letter, both through the
LLM gateway. A claim the rules or the threshold stop costs nothing. The gateway needs its
`LLM_` settings keys.

`--rules-today` is the date the rules take as "today". It has no default, because the rules
never read the clock; the made-up deadlines run from 2008 to 2011. The amount floor comes
from the `RULES_AMOUNT_FLOOR_USD` setting and the score threshold from `AGENT_WIN_THRESHOLD`.

Only a denied claim whose letter has a current extraction is selected; `--limit` takes the
first of them in claim id order, so the same limit always means the same claims. A claim
whose stored trace ended as `needs_review`, `needs_review_no_draft`, `stopped_by_rules` or
`below_threshold` is skipped, also when the date, the floor or the threshold has changed
since. A claim that ended as `draft_failed` is taken again and its trace is replaced. When
the monthly budget is reached, no provider can answer, a provider refuses a request, the
evidence has no stored vector or a search question's vector cannot be used, the run stops,
stores no trace for that claim and keeps what it finished.

Every end state is a stop for a person. A stored draft waits for review; nothing is sent
anywhere.

At the end the command prints the counts per end state, the time and the money spent. It
prints no letter text and no document text.

Exit codes: 0 every selected claim was looked at, 1 the run stopped early or could not start
(no saved model, stored labels that no longer fit, no claim to take), 2 bad arguments.
"""

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime

from src.agent.run import run_agent
from src.agent.store import ClaimRowsError, select_agent_candidates
from src.agent.trace import AGENT_VERSION, DraftFailure
from src.config.settings import get_settings, load_llm_gateway_settings
from src.db.models import DatasetSplit, EndState
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.drafting.prompts import (
    DRAFTING_PROMPT_VERSION,
    DraftingPromptError,
    load_drafting_prompt,
)
from src.drafting.store import LetterExtractionError, count_claims_left_out
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.llm.budget import month_to_date_spend
from src.llm.openai_provider import build_openai_gateway
from src.ml.inference import ModelFileError, load_win_model

EXIT_OK = 0
EXIT_STOPPED = 1


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a date like 2010-01-01") from None


def main(argv: Sequence[str] | None = None) -> int:
    """Run the agent loop for the denied claims of one split and store the traces.

    Spends money: one embedding call and one model call, several seconds each, per claim that
    passes the rules and the score threshold.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--split",
        type=DatasetSplit,
        choices=list(DatasetSplit),
        required=True,
        help="the dataset split whose denied claims are taken",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="only the first this many claims of the split (default: all of them)",
    )
    parser.add_argument(
        "--rules-today",
        type=_day,
        required=True,
        help="the date the rules take as today, as YYYY-MM-DD (no default)",
    )
    parser.add_argument(
        "--prompt-version",
        default=DRAFTING_PROMPT_VERSION,
        help=f"the version of the drafting prompt (default {DRAFTING_PROMPT_VERSION})",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        prompt = load_drafting_prompt(settings.prompt_dir, args.prompt_version)
    except DraftingPromptError as exc:
        parser.error(str(exc))
    gateway_settings = load_llm_gateway_settings()
    try:
        model = load_win_model(settings.model_dir)
    except ModelFileError as exc:
        print(f"{exc}: run pipelines.train_win_model first", file=sys.stderr)
        return EXIT_STOPPED
    except NotADirectoryError:
        print("the MODEL_DIR setting names a file, not a folder", file=sys.stderr)
        return EXIT_STOPPED

    logging.basicConfig(level=settings.log_level)
    engine = create_db_engine()
    try:
        session_factory = create_session_factory(engine)
        with session_scope(session_factory) as session:
            try:
                candidates = select_agent_candidates(
                    session, args.split, EXTRACTION_PROMPT_VERSION, args.limit
                )
            except (LetterExtractionError, ClaimRowsError) as exc:
                print(f"cannot read the claims to take: {exc}", file=sys.stderr)
                return EXIT_STOPPED
            left_out = count_claims_left_out(session, args.split, EXTRACTION_PROMPT_VERSION)
            spent_before = month_to_date_spend(session, datetime.now(UTC))
        if not candidates:
            print(
                f"the {args.split.value} split has no denied claim with a current letter"
                " extraction: run pipelines.extract_documents first",
                file=sys.stderr,
            )
            return EXIT_STOPPED

        gateway = build_openai_gateway(gateway_settings, session_factory)
        started = time.monotonic()
        summary = run_agent(
            session_factory,
            gateway,
            prompt,
            model,
            candidates,
            today=args.rules_today,
            amount_floor=settings.rules_amount_floor_usd,
            win_threshold=settings.agent_win_threshold,
            embedding_model=gateway_settings.llm_embedding_model,
        )
        seconds = time.monotonic() - started

        with session_scope(session_factory) as session:
            spent_after = month_to_date_spend(session, datetime.now(UTC))
    finally:
        engine.dispose()

    print(
        f"ran the agent loop for {summary.stored} of {summary.selected} selected claims"
        f" ({args.split.value} split, agent {AGENT_VERSION}, drafting prompt"
        f" {args.prompt_version})"
    )
    print(
        f"  rules today {args.rules_today.isoformat()}, amount floor"
        f" ${settings.rules_amount_floor_usd}, score threshold {settings.agent_win_threshold:.2f}"
    )
    print(f"  finished before this run: {summary.already_finished}")
    for state in EndState:
        print(f"  {state.value}: {summary.end_states[state.value]}")
        if state is EndState.NEEDS_REVIEW:
            print(f"    drafted with at least one citation: {summary.drafted_with_citation}")
        if state is EndState.DRAFT_FAILED:
            for failure in DraftFailure:
                print(f"    {failure.value}: {summary.draft_failures.get(failure.value, 0)}")
            print(f"    no rules verdict: {summary.rules_failed}")
    print(f"  answered by the fallback: {summary.answered_by_fallback}")
    print(f"  denied claims of the split left out (no current letter extraction): {left_out}")
    print(f"  time: {seconds:.0f} s")
    print(
        f"  spent ${spent_after - spent_before} in this run; ${spent_after} of the"
        f" ${gateway_settings.llm_monthly_budget_usd} monthly budget is used"
    )
    if summary.stop_reason is not None:
        print(
            f"stopped early: {summary.stop_reason}; {summary.not_tried} claims were not"
            " tried. Run the same command again to continue.",
            file=sys.stderr,
        )
        return EXIT_STOPPED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
