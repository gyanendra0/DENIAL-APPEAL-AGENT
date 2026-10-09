"""Made-up stored claims for the win-model run tests. No real data.

The six-claim fixture file has three denied claims, too few to train on, so these tests store
a few hundred invented claims instead. Every third claim is paid; the others are denied, half
for medical necessity (partly denied, a high allowed charge: the label rule's chance is 0.98)
and half as a duplicate (fully denied, no allowed charge: chance 0.02). So the proxy follows
the category almost always and a tiny model has something to learn.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from src.db.models import ClaimSample, ClaimSampleLabel, ClaimSampleLine
from src.ml.labels import label_claim
from src.ml.splits import assign_split

SPLIT_SEED = 42
CLAIM_COUNT = 360
DENIED_COUNT = 240
PAID_CLAIM = "810000000000002"
NECESSITY_CLAIM = "810000000000000"
DUPLICATE_CLAIM = "810000000000001"


def claim_id(number: int) -> str:
    return f"{810000000000000 + number}"


def _line(
    number: int, line_number: int, indicator: str, payment: str, allowed: str
) -> ClaimSampleLine:
    return ClaimSampleLine(
        source_claim_id=claim_id(number),
        line_number=line_number,
        hcpcs_code="99213",
        line_diagnosis_code=None,
        processing_indicator=indicator,
        payment_amount=Decimal(payment),
        deductible_amount=Decimal("0.00"),
        primary_payer_paid_amount=Decimal("0.00"),
        coinsurance_amount=Decimal("0.00"),
        allowed_charge_amount=Decimal(allowed),
    )


def _lines(number: int) -> list[ClaimSampleLine]:
    kind = number % 3
    if kind == 0:  # medical necessity, partly denied, total allowed charge 300.00
        return [
            _line(number, 1, "N", "0.00", "0.00"),
            _line(number, 2, "A", "240.00", "300.00"),
        ]
    if kind == 1:  # duplicate, fully denied, no allowed charge
        return [_line(number, 1, "M", "0.00", "0.00")]
    return [_line(number, 1, "A", "40.00", "50.00")]  # paid


def store_made_up_claims(session: Session, count: int = CLAIM_COUNT) -> None:
    """Store `count` claims with their lines and labels, the way the data pipeline would."""
    for number in range(count):
        session.add(
            ClaimSample(
                source_claim_id=claim_id(number),
                claim_from_date=date(2009, 3, 1),
                claim_thru_date=date(2009, 3, 1),
                diagnosis_codes=[],
            )
        )
    session.flush()
    for number in range(count):
        lines = _lines(number)
        session.add_all(lines)
        label = label_claim(claim_id(number), lines)
        session.add(
            ClaimSampleLabel(
                **label.model_dump(),
                split=assign_split(claim_id(number), SPLIT_SEED),
                split_seed=SPLIT_SEED,
            )
        )
    session.flush()
