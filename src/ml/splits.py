"""Assign a claim to the train, validation or test split.

The split is made by claim, never by document, so everything that belongs to one claim stays
on one side. Each claim is placed on its own from a hash of the seed and the claim id: the
same claim and seed always give the same split, whatever other claims are loaded. The split
is not stratified, so the share of denied claims can differ a little between splits.

The hash input starts with `split:` so that it never equals the input of the label draw in
`src.ml.labels`; the split is then independent of the proxy label.
"""

from decimal import Decimal
from fractions import Fraction

from src.db.models import DatasetSplit
from src.ml.draws import repeatable_draw

TRAIN_SHARE = Decimal("0.70")
VALIDATION_SHARE = Decimal("0.15")  # the test split is the rest
MAX_SPLIT_SEED = 2**31 - 1  # the seed is stored in an integer column


def assign_split(source_claim_id: str, seed: int) -> DatasetSplit:
    """Return the split of one claim. The same claim id and seed always give the same split."""
    if not 0 <= seed <= MAX_SPLIT_SEED:
        raise ValueError(f"split seed must be between 0 and {MAX_SPLIT_SEED}")
    draw = repeatable_draw(f"split:{seed}:{source_claim_id}")
    if draw < Fraction(TRAIN_SHARE):
        return DatasetSplit.TRAIN
    if draw < Fraction(TRAIN_SHARE + VALIDATION_SHARE):
        return DatasetSplit.VALIDATION
    return DatasetSplit.TEST
