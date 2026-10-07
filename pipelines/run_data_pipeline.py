"""Run the Stage 1 data pipeline: load both public files, then label and split the claims.

Usage:
    python3 -m pipelines.run_data_pipeline <path-to-xlsx> <path-to-zip> --plan-year 2026
        [--max-claims 50000] [--split-seed 42]

The first file is the CMS Transparency in Coverage PUF workbook, the second the CMS DE-SynPUF
carrier claims file. Both are checked, and the labels are built and checked, before anything
is written; then all five tables are written in one transaction and a quality report is
printed. Issuers, plans, claims and lines are updated in place and never removed. The labels
are replaced: afterwards only the claims of this run have a label. The appeal-success label
is a proxy, not an observed outcome.

Exit codes: 0 loaded, 1 a file or the class balance was rejected by a quality gate (nothing
written), 2 bad arguments.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pipelines.load_claims_sample import DEFAULT_MAX_CLAIMS
from src.config.settings import get_settings
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.ingest.claims_sample import read_claim_sample_rows, upsert_claim_sample_batch
from src.ingest.marketplace_denials import (
    BatchRejectedError,
    read_issuer_denial_rows,
    upsert_issuer_denial_rows,
)
from src.ingest.marketplace_plan_denials import read_plan_denial_rows, upsert_plan_denial_rows
from src.ml.claim_labels import (
    build_appeal_success_chances,
    build_claim_label_rows,
    replace_claim_label_rows,
)
from src.ml.quality_report import build_label_quality_report
from src.ml.splits import MAX_SPLIT_SEED

EXIT_OK = 0
EXIT_REJECTED = 1
DEFAULT_SPLIT_SEED = 42


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _split_seed(text: str) -> int:
    value = int(text)
    if not 0 <= value <= MAX_SPLIT_SEED:
        raise argparse.ArgumentTypeError(f"must be between 0 and {MAX_SPLIT_SEED}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Check both files, label and split the claims, and write everything in one transaction.

    With the default of 50,000 claims this takes about a minute.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "marketplace_path", type=Path, help="the Transparency in Coverage PUF workbook (.xlsx)"
    )
    parser.add_argument(
        "claims_path", type=Path, help="the carrier claims file (.zip holding one .csv)"
    )
    parser.add_argument(
        "--plan-year", type=int, required=True, help="plan year of the workbook, e.g. 2026"
    )
    parser.add_argument(
        "--max-claims",
        type=_positive_int,
        default=DEFAULT_MAX_CLAIMS,
        help=f"how many claims to read from the start of the file (default {DEFAULT_MAX_CLAIMS})",
    )
    parser.add_argument(
        "--split-seed",
        type=_split_seed,
        default=DEFAULT_SPLIT_SEED,
        help=f"seed of the train / validation / test split (default {DEFAULT_SPLIT_SEED})",
    )
    args = parser.parse_args(argv)
    for path in (args.marketplace_path, args.claims_path):
        if not path.is_file():
            parser.error(f"file not found: {path}")

    logging.basicConfig(level=get_settings().log_level)
    try:
        issuer_rows = read_issuer_denial_rows(args.marketplace_path, args.plan_year)
        plan_rows = read_plan_denial_rows(args.marketplace_path, args.plan_year)
        batch = read_claim_sample_rows(args.claims_path, args.max_claims)
    except BatchRejectedError as exc:
        print(exc, file=sys.stderr)
        return EXIT_REJECTED

    label_rows = build_claim_label_rows(
        (claim.source_claim_id for claim in batch.claims), batch.lines, args.split_seed
    )
    chances = build_appeal_success_chances(
        (claim.source_claim_id for claim in batch.claims), batch.lines
    )
    report = build_label_quality_report(label_rows, chances)
    print(report.as_text())
    problems = report.problems()
    if problems:
        print(BatchRejectedError(problems), file=sys.stderr)
        return EXIT_REJECTED

    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            issuers = upsert_issuer_denial_rows(session, issuer_rows)
            plans = upsert_plan_denial_rows(session, plan_rows)  # after the issuers they point at
            claims, lines = upsert_claim_sample_batch(session, batch)
            labels = replace_claim_label_rows(session, label_rows)  # after the claims
    finally:
        engine.dispose()
    print(
        f"loaded {issuers} issuer rows, {plans} plan rows, {claims} claims,"
        f" {lines} service lines and {labels} claim labels"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
