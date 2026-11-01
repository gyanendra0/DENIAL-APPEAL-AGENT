"""Train a tiny model on made-up rows, for the `kind` smoke test.

Usage:
    python3 -m pipelines.make_smoke_model --out <folder>

The API loads its model when it starts, and `models/` is not in the repository, so a throwaway
cluster needs one of its own. Run it inside the API image: the loader refuses a model made with
another scikit-learn version. The scores of this model mean nothing, so it never serves a user.

Exit codes: 0 the two model files were written.
"""

import argparse
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from src.db.models import DatasetSplit, DenialReasonCategory
from src.ml.features import ClaimFeatures
from src.ml.training import SplitFacts, TrainingRow, save_win_model, train_win_model

EXIT_OK = 0

SMOKE_SEED = 42
# Two leaves of `MIN_CLAIMS_PER_LEAF` claims, so one split can be made.
SMOKE_ROWS = 120


def smoke_rows() -> list[TrainingRow]:
    """Made-up claims with both proxy values and no link to any real claim."""
    return [
        TrainingRow(
            features=ClaimFeatures(
                denial_reason_category=DenialReasonCategory.OTHER,
                total_allowed_charge=Decimal(100 + number),
                fully_denied=number % 2 == 0,
            ),
            appeal_success_proxy=number % 2 == 0,
        )
        for number in range(SMOKE_ROWS)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="Folder for the two model files.")
    args = parser.parse_args(argv)

    rows = smoke_rows()
    proxy_true = sum(row.appeal_success_proxy for row in rows)
    save_win_model(
        train_win_model(rows, seed=SMOKE_SEED),
        args.out,
        split_seed=SMOKE_SEED,
        splits={DatasetSplit.TRAIN: SplitFacts(rows=len(rows), proxy_true=proxy_true, auc=None)},
    )
    print(f"smoke model written to {args.out}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
