"""Extract typed fields from the stored documents of one dataset split.

Usage:
    python3 -m pipelines.extract_documents --split validation [--limit 50] [--prompt-version v2]

Run `pipelines.generate_documents` first: this command reads the documents it stored. Each
document is sent to the model through the LLM gateway, once, and its result is saved in
`document_extractions` as soon as it arrives. THIS SPENDS MONEY: one model call per document
that has no result yet. The gateway needs its `LLM_` settings keys.

A document that already has a result for the prompt version, made from the text it has now,
is skipped, so a run that stopped can be started again. `--limit` takes the first documents
of the split in claim id order, so the same limit always means the same documents. An answer
that is not usable (not JSON, not the schema, or cut off at the output limit) is counted and
nothing is stored for it. When the monthly budget is reached, no provider can answer, or a
provider refuses the request (a bad request, a wrong key), the run stops and keeps what it
finished.

At the end the command prints the counts, the time, the money spent, and how many extracted
values equal the answer keys exactly (a count for comparing prompts, not the accuracy gate).
Documents with no result are not in that count; the command prints how many there are.

Exit codes: 0 every selected document was looked at, 1 the run stopped early (budget reached,
no provider, or a refused request) or the split has no document, 2 bad arguments.
"""

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from src.config.settings import get_settings, load_llm_gateway_settings
from src.db.models import DatasetSplit, DocumentType
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.extraction.accuracy import count_field_matches
from src.extraction.prompts import (
    EXTRACTION_PROMPT_VERSION,
    PromptError,
    load_extraction_prompt,
)
from src.extraction.run import run_extraction
from src.extraction.store import read_extraction_rows, select_documents
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
    """Extract the documents of one split and store the results.

    Spends money: one model call per document without a result. The 1,883 documents of the
    test split took 103 minutes (measured 2026-10-09).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--split",
        type=DatasetSplit,
        choices=list(DatasetSplit),
        required=True,
        help="the dataset split whose documents are extracted",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="only the first this many documents of the split (default: all of them)",
    )
    parser.add_argument(
        "--prompt-version",
        default=EXTRACTION_PROMPT_VERSION,
        help=f"the version of the extraction prompts (default {EXTRACTION_PROMPT_VERSION})",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        prompts = {
            document_type: load_extraction_prompt(
                settings.prompt_dir, args.prompt_version, document_type
            )
            for document_type in DocumentType
        }
    except PromptError as exc:
        parser.error(str(exc))
    gateway_settings = load_llm_gateway_settings()

    logging.basicConfig(level=settings.log_level)
    engine = create_db_engine()
    try:
        session_factory = create_session_factory(engine)
        with session_scope(session_factory) as session:
            documents = select_documents(session, args.split, args.limit)
            spent_before = month_to_date_spend(session, datetime.now(UTC))
        if not documents:
            print(
                f"the {args.split.value} split has no document: run"
                " pipelines.generate_documents first",
                file=sys.stderr,
            )
            return EXIT_STOPPED

        gateway = build_openai_gateway(gateway_settings, session_factory)
        started = time.monotonic()
        summary = run_extraction(session_factory, gateway, prompts, documents)
        seconds = time.monotonic() - started

        with session_scope(session_factory) as session:
            rows = read_extraction_rows(session, args.prompt_version)
            spent_after = month_to_date_spend(session, datetime.now(UTC))
    finally:
        engine.dispose()

    print(
        f"extracted {summary.extracted} of {summary.selected} selected documents"
        f" ({args.split.value} split, prompt {args.prompt_version})"
    )
    print(f"  already extracted before this run: {summary.already_extracted}")
    print(f"  failed to parse: {summary.failed_to_parse}")
    print(f"  stopped for length: {summary.stopped_for_length}")
    print(f"  answered by the fallback: {summary.answered_by_fallback}")
    print(f"  time: {seconds:.0f} s")
    print(
        f"  spent ${spent_after - spent_before} in this run; ${spent_after} of the"
        f" ${gateway_settings.llm_monthly_budget_usd} monthly budget is used"
    )
    print(count_field_matches(documents, rows).as_text())
    if summary.stop_reason is not None:
        print(
            f"stopped early: {summary.stop_reason}; {summary.not_tried} documents were not"
            " tried. Run the same command again to continue.",
            file=sys.stderr,
        )
        return EXIT_STOPPED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
