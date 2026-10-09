"""Made-up training rows for the win-model tests. No real data.

The proxy follows a simple invented rule, so a tiny model has something to learn. There are
enough rows (288) for the model's smallest leaf of 50 claims to allow a split.
"""

from decimal import Decimal

from src.db.models import DenialReasonCategory
from src.ml.features import ClaimFeatures
from src.ml.training import TrainingRow

AMOUNTS = ("0.00", "40.00", "90.00", "150.00", "300.00", "800.00")
COPIES = 4


def features(
    category: DenialReasonCategory, amount: str = "90.00", *, fully_denied: bool = False
) -> ClaimFeatures:
    return ClaimFeatures(
        denial_reason_category=category,
        total_allowed_charge=Decimal(amount),
        fully_denied=fully_denied,
    )


def _made_up_proxy(row: ClaimFeatures) -> bool:
    if row.denial_reason_category is DenialReasonCategory.MEDICAL_NECESSITY:
        return True
    if row.denial_reason_category is DenialReasonCategory.DUPLICATE:
        return False
    return row.total_allowed_charge >= 150 and not row.fully_denied


def training_rows(*, without: DenialReasonCategory | None = None) -> list[TrainingRow]:
    """Every category, amount and fully-denied value, `COPIES` times; `without` leaves one out."""
    rows = []
    for category in DenialReasonCategory:
        if category is without:
            continue
        for amount in AMOUNTS:
            for fully_denied in (False, True):
                row = features(category, amount, fully_denied=fully_denied)
                rows.extend(
                    [TrainingRow(features=row, appeal_success_proxy=_made_up_proxy(row))] * COPIES
                )
    return rows
