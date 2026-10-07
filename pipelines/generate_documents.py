"""Generate the Stage 2 documents: one fabricated denial letter per denied claim.

Usage:
    python3 -m pipelines.generate_documents [--seed 42]

Run `pipelines.run_data_pipeline` first: this command reads the claims, lines and labels it
stored. Every claim that is labelled denied gets one denial letter. The documents are
replaced, all in one transaction: afterwards the table holds only the documents of this run,
so it never mixes two seeds or two generator versions. The same claims, seed and generator
version always give the same documents. Every name, id and letter date in a document is made
up.

Exit codes: 0 generated, 1 the stored labels cannot be used (none denied, made by another
label rule version, or a denied claim whose stored lines hold no denied line; nothing
written), 2 bad arguments.
"""

import argparse
import logging
import sys
from collections.abc import Sequence

from src.config.settings import get_settings
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.ingest.marketplace_denials import BatchRejectedError
from src.synth.denial_letter import GENERATOR_VERSION
from src.synth.documents import build_denial_letter_rows, replace_generated_document_rows
from src.synth.identity import MAX_SEED

EXIT_OK = 0
EXIT_REJECTED = 1
DEFAULT_SEED = 42


def _seed(text: str) -> int:
    value = int(text)
    if not 0 <= value <= MAX_SEED:
        raise argparse.ArgumentTypeError(f"must be between 0 and {MAX_SEED}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Write a denial letter for every denied claim and replace the stored documents.

    With the default load of 50,000 claims this takes a few seconds.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--seed",
        type=_seed,
        default=DEFAULT_SEED,
        help=f"seed of every made-up value in the documents (default {DEFAULT_SEED})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=get_settings().log_level)
    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            rows = build_denial_letter_rows(session, args.seed)
            written = replace_generated_document_rows(session, rows)
    except BatchRejectedError as exc:
        print(exc, file=sys.stderr)
        return EXIT_REJECTED
    finally:
        engine.dispose()
    print(f"generated {written} denial letters (seed {args.seed}, generator {GENERATOR_VERSION})")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
