from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

import pytest

from src.db.models import DatasetSplit
from src.ml.labels import label_claim
from src.ml.splits import MAX_SPLIT_SEED, assign_split

SEED = 42
CLAIM_IDS = [f"{900000000000000 + n}" for n in range(10_000)]


@dataclass(frozen=True)
class Line:
    """A made-up service line with the four fields the label rule reads."""

    line_number: int
    processing_indicator: str
    payment_amount: Decimal
    allowed_charge_amount: Decimal


def test_same_claim_and_seed_always_give_the_same_split() -> None:
    first = [assign_split(claim_id, SEED) for claim_id in CLAIM_IDS[:200]]
    second = [assign_split(claim_id, SEED) for claim_id in reversed(CLAIM_IDS[:200])]

    assert first == list(reversed(second))


# Worked out once from the hash recipe (SHA-256 of "split:<seed>:<claim id>"); a change to
# the recipe or to the order of the splits moves them.
@pytest.mark.parametrize(
    ("claim_id", "seed", "split"),
    [
        ("800000000000001", 42, DatasetSplit.TRAIN),
        ("800000000000007", 42, DatasetSplit.VALIDATION),
        ("800000000000009", 42, DatasetSplit.TEST),
        ("800000000000002", 7, DatasetSplit.VALIDATION),
        ("800000000000004", 7, DatasetSplit.TEST),
        ("800000000000009", 7, DatasetSplit.TRAIN),
    ],
)
def test_known_claims_land_in_their_known_split(
    claim_id: str, seed: int, split: DatasetSplit
) -> None:
    assert assign_split(claim_id, seed) is split


def test_another_seed_moves_some_claims_to_another_split() -> None:
    moved = sum(assign_split(claim_id, SEED) != assign_split(claim_id, 7) for claim_id in CLAIM_IDS)

    assert 0.30 < moved / len(CLAIM_IDS) < 0.60


def test_shares_are_close_to_70_15_15() -> None:
    counts = Counter(assign_split(claim_id, SEED) for claim_id in CLAIM_IDS)
    shares = {split: counts[split] / len(CLAIM_IDS) for split in DatasetSplit}

    assert shares[DatasetSplit.TRAIN] == pytest.approx(0.70, abs=0.02)
    assert shares[DatasetSplit.VALIDATION] == pytest.approx(0.15, abs=0.02)
    assert shares[DatasetSplit.TEST] == pytest.approx(0.15, abs=0.02)


def test_split_does_not_depend_on_the_proxy_label() -> None:
    lines = [Line(1, "O", Decimal("0.00"), Decimal("50.00"))]  # denied, chance 0.45
    true_by_split: Counter[DatasetSplit] = Counter()
    total_by_split: Counter[DatasetSplit] = Counter()
    for claim_id in CLAIM_IDS:
        split = assign_split(claim_id, SEED)
        total_by_split[split] += 1
        true_by_split[split] += label_claim(claim_id, lines).appeal_success_proxy is True

    for split in DatasetSplit:
        assert true_by_split[split] / total_by_split[split] == pytest.approx(0.45, abs=0.05)


@pytest.mark.parametrize("seed", [0, MAX_SPLIT_SEED])
def test_accepts_the_smallest_and_largest_seed(seed: int) -> None:
    assert assign_split("800000000000001", seed) in DatasetSplit


@pytest.mark.parametrize("seed", [-1, MAX_SPLIT_SEED + 1])
def test_rejects_a_seed_that_does_not_fit_the_integer_column(seed: int) -> None:
    with pytest.raises(ValueError, match="split seed must be between"):
        assign_split("800000000000001", seed)
