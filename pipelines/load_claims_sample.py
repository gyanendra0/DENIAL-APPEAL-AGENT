"""Load the CMS DE-SynPUF carrier claims file into `claim_samples` and `claim_sample_lines`.

Usage:
    python3 -m pipelines.load_claims_sample <path-to-zip> [--max-claims 50000]

Exit codes: 0 loaded, 1 file rejected by a quality gate (nothing written), 2 bad arguments.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from src.config.settings import get_settings
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.ingest.claims_sample import read_claim_sample_rows, upsert_claim_sample_batch
from src.ingest.marketplace_denials import BatchRejectedError

EXIT_OK = 0
EXIT_REJECTED = 1
# The full file has 2.4 million claims; the first 50,000 are enough to work with.
DEFAULT_MAX_CLAIMS = 50_000


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Read, validate and upsert the first claims of one file in a single transaction.

    With the default of 50,000 claims this takes about half a minute.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="the carrier claims file (.zip holding one .csv)")
    parser.add_argument(
        "--max-claims",
        type=_positive_int,
        default=DEFAULT_MAX_CLAIMS,
        help=f"how many claims to read from the start of the file (default {DEFAULT_MAX_CLAIMS})",
    )
    args = parser.parse_args(argv)
    if not args.path.is_file():
        parser.error(f"file not found: {args.path}")

    logging.basicConfig(level=get_settings().log_level)
    try:
        batch = read_claim_sample_rows(args.path, args.max_claims)
    except BatchRejectedError as exc:
        print(exc, file=sys.stderr)
        return EXIT_REJECTED

    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            claims, lines = upsert_claim_sample_batch(session, batch)
    finally:
        engine.dispose()
    print(f"loaded {claims} claims and {lines} service lines")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
