"""Draft a cited appeal letter for the denied claims of one dataset split.

Usage:
    python3 -m pipelines.draft_appeals --split test [--limit 50] [--prompt-version v2]

Run `pipelines.extract_documents` and `pipelines.embed_evidence_corpus` first: this command
reads the extracted fields of each claim's denial letter and searches the embedded evidence.
THIS SPENDS MONEY: per claim without a draft, one small embedding call for the search question
and one model call for the letter, both through the LLM gateway. The gateway needs its `LLM_`
settings keys.

Only a denied claim whose letter has a current extraction is selected; `--limit` takes the
first of them in claim id order, so the same limit always means the same claims. A claim that
already has a draft of the prompt version is skipped, so a run that stopped can be started
again. A draft the citation validator blocks, a draft with text in square brackets, and an
answer that is not usable (not JSON, not the schema, or cut off at the output limit), is
counted and nothing is stored for it; the run goes on. When the monthly budget is reached, no
provider can answer, a provider refuses a request (a bad request, a wrong key), the evidence
has no stored vector or a search question's vector cannot be used, the run stops and keeps
what it finished.

A stored draft is for a person to review. Nothing is sent anywhere.

At the end the command prints the counts, the time and the money spent. It prints no letter
text and no document text.

Exit codes: 0 every selected claim was looked at, 1 the run stopped early or the split has no
claim to draft, 2 bad arguments.
"""

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from src.config.settings import get_settings, load_llm_gateway_settings
from src.db.models import DatasetSplit
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.drafting.citation_validator import CitationProblemKind
from src.drafting.prompts import (
    DRAFTING_PROMPT_VERSION,
    DraftingPromptError,
    load_drafting_prompt,
)
from src.drafting.run import run_drafting
from src.drafting.search_question import SEARCH_QUESTION_VERSION
from src.drafting.store import (
    LetterExtractionError,
    count_claims_left_out,
    select_draft_candidates,
)
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.llm.budget import month_to_date_spend
from src.llm.openai_provider import build_openai_gateway

EXIT_OK = 0
EXIT_STOPPED = 1


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Draft an appeal for the denied claims of one split and store the checked letters.

    Spends money: one embedding call and one model call per claim without a draft, several
    seconds each.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--split",
        type=DatasetSplit,
        choices=list(DatasetSplit),
        required=True,
        help="the dataset split whose denied claims are drafted",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="only the first this many claims of the split (default: all of them)",
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

    logging.basicConfig(level=settings.log_level)
    engine = create_db_engine()
    try:
        session_factory = create_session_factory(engine)
        with session_scope(session_factory) as session:
            try:
                candidates = select_draft_candidates(
                    session, args.split, EXTRACTION_PROMPT_VERSION, args.limit
                )
            except LetterExtractionError as exc:
                print(f"cannot read the claims to draft: {exc}", file=sys.stderr)
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
        summary = run_drafting(
            session_factory,
            gateway,
            prompt,
            candidates,
            embedding_model=gateway_settings.llm_embedding_model,
        )
        seconds = time.monotonic() - started

        with session_scope(session_factory) as session:
            spent_after = month_to_date_spend(session, datetime.now(UTC))
    finally:
        engine.dispose()

    print(
        f"drafted {summary.drafted} of {summary.selected} selected claims"
        f" ({args.split.value} split, prompt {args.prompt_version},"
        f" search question {SEARCH_QUESTION_VERSION})"
    )
    print(f"  already drafted before this run: {summary.already_drafted}")
    print(f"  drafted with at least one citation: {summary.drafted_with_citation}")
    print(f"  blocked by the citation validator: {summary.blocked}")
    for kind in CitationProblemKind:
        print(f"    {kind.value}: {summary.blocked_by_kind.get(kind.value, 0)}")
    print(f"  failed to parse: {summary.failed_to_parse}")
    print(f"  stopped for length: {summary.stopped_for_length}")
    print(f"  refused for placeholder text: {summary.refused_for_placeholder}")
    print(f"  refused by the fact check: {summary.refused_for_fact_check}")
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
