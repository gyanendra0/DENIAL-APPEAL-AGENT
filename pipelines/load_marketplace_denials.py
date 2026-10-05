"""Load the CMS Transparency in Coverage PUF into `issuer_denial_stats` and `plan_denial_stats`.

Usage:
    python3 -m pipelines.load_marketplace_denials <path-to-xlsx> --plan-year 2026

Exit codes: 0 loaded, 1 file rejected by a quality gate (nothing written), 2 bad arguments.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from src.config.settings import get_settings
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.ingest.marketplace_denials import (
    BatchRejectedError,
    read_issuer_denial_rows,
    upsert_issuer_denial_rows,
)
from src.ingest.marketplace_plan_denials import read_plan_denial_rows, upsert_plan_denial_rows

EXIT_OK = 0
EXIT_REJECTED = 1


def main(argv: Sequence[str] | None = None) -> int:
    """Read, validate and upsert one workbook in a single transaction. Takes a few seconds."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="the Transparency in Coverage PUF workbook (.xlsx)")
    parser.add_argument(
        "--plan-year", type=int, required=True, help="plan year of the file, e.g. 2026"
    )
    args = parser.parse_args(argv)
    if not args.path.is_file():
        parser.error(f"file not found: {args.path}")

    logging.basicConfig(level=get_settings().log_level)
    try:
        issuer_rows = read_issuer_denial_rows(args.path, args.plan_year)
        plan_rows = read_plan_denial_rows(args.path, args.plan_year)
    except BatchRejectedError as exc:
        print(exc, file=sys.stderr)
        return EXIT_REJECTED

    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            issuers = upsert_issuer_denial_rows(session, issuer_rows)
            plans = upsert_plan_denial_rows(session, plan_rows)  # after the issuers they point at
    finally:
        engine.dispose()
    print(f"loaded {issuers} issuer rows and {plans} plan rows for plan year {args.plan_year}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
