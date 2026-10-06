from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import ClaimSample, ClaimSampleLabel, DatasetSplit, DenialReasonCategory
from src.ml.claim_labels import (
    ClaimLabelRow,
    build_claim_label_rows,
    replace_claim_label_rows,
    upsert_claim_label_rows,
)
from src.ml.splits import MAX_SPLIT_SEED

SEED = 42
PAID_CLAIM = "800000000000001"  # split with seed 42: train
DENIED_CLAIM = "800000000000007"  # split with seed 42: validation


@dataclass(frozen=True)
class Line:
    """A made-up service line."""

    source_claim_id: str
    line_number: int
    processing_indicator: str
    payment_amount: Decimal
    allowed_charge_amount: Decimal


LINES = [
    Line(DENIED_CLAIM, 2, "N", Decimal("0.00"), Decimal("0.00")),
    Line(PAID_CLAIM, 1, "A", Decimal("40.00"), Decimal("50.00")),
    Line(DENIED_CLAIM, 1, "A", Decimal("80.00"), Decimal("100.00")),
]


def _rows() -> list[ClaimLabelRow]:
    return build_claim_label_rows([PAID_CLAIM, DENIED_CLAIM], LINES, SEED)


def _store_claims(session: Session, *claim_ids: str) -> None:
    for claim_id in claim_ids:
        session.add(
            ClaimSample(
                source_claim_id=claim_id,
                claim_from_date=date(2009, 3, 1),
                claim_thru_date=date(2009, 3, 2),
                diagnosis_codes=[],
            )
        )
    session.flush()


def _stored(session: Session) -> dict[str, ClaimSampleLabel]:
    session.expire_all()
    return {label.source_claim_id: label for label in session.scalars(select(ClaimSampleLabel))}


def test_builds_one_row_per_claim_with_its_label_split_and_seed() -> None:
    paid, denied = _rows()

    assert (paid.source_claim_id, paid.is_denied, paid.split) == (
        PAID_CLAIM,
        False,
        DatasetSplit.TRAIN,
    )
    assert (denied.source_claim_id, denied.is_denied, denied.split) == (
        DENIED_CLAIM,
        True,
        DatasetSplit.VALIDATION,
    )
    assert denied.denial_reason_category is DenialReasonCategory.MEDICAL_NECESSITY
    assert denied.appeal_success_proxy is not None
    assert paid.label_rule_version == denied.label_rule_version == "v1"
    assert paid.split_seed == denied.split_seed == SEED


def test_each_claim_is_labelled_from_its_own_lines_only() -> None:
    # The denied line belongs to the second claim; the first must stay not denied.
    paid, _ = _rows()

    assert paid.denial_reason_category is None
    assert paid.appeal_success_proxy is None


def test_rejects_a_claim_without_lines() -> None:
    with pytest.raises(ValueError, match="800000000000009 has no lines"):
        build_claim_label_rows([PAID_CLAIM, "800000000000009"], LINES[1:2], SEED)


def test_rejects_a_line_of_a_claim_that_is_not_given() -> None:
    with pytest.raises(ValueError, match=f"claim {DENIED_CLAIM}, which is not given"):
        build_claim_label_rows([PAID_CLAIM], LINES, SEED)


def test_rejects_a_seed_that_does_not_fit_the_integer_column() -> None:
    with pytest.raises(ValueError, match="split seed must be between"):
        build_claim_label_rows([PAID_CLAIM], LINES[1:2], MAX_SPLIT_SEED + 1)


def test_row_model_rejects_a_seed_outside_the_integer_column() -> None:
    fields = _rows()[0].model_dump() | {"split_seed": -1}

    with pytest.raises(ValidationError, match="split_seed"):
        ClaimLabelRow(**fields)


def test_upsert_stores_the_rows(session: Session) -> None:
    _store_claims(session, PAID_CLAIM, DENIED_CLAIM)

    written = upsert_claim_label_rows(session, _rows())

    stored = _stored(session)
    assert written == 2
    assert stored[PAID_CLAIM].is_denied is False
    assert stored[PAID_CLAIM].denial_reason_category is None
    assert stored[PAID_CLAIM].split is DatasetSplit.TRAIN
    assert stored[DENIED_CLAIM].is_denied is True
    assert stored[DENIED_CLAIM].denial_reason_category is DenialReasonCategory.MEDICAL_NECESSITY
    assert stored[DENIED_CLAIM].appeal_success_proxy is _rows()[1].appeal_success_proxy
    assert stored[DENIED_CLAIM].split is DatasetSplit.VALIDATION
    assert stored[DENIED_CLAIM].split_seed == SEED
    assert stored[DENIED_CLAIM].label_rule_version == "v1"


def test_upsert_twice_updates_in_place_and_adds_no_rows(session: Session) -> None:
    _store_claims(session, PAID_CLAIM, DENIED_CLAIM)
    upsert_claim_label_rows(session, _rows())
    first_id = _stored(session)[DENIED_CLAIM].id
    relabelled = [
        ClaimLabelRow(
            source_claim_id=DENIED_CLAIM,
            is_denied=False,
            denial_reason_category=None,
            appeal_success_proxy=None,
            label_rule_version="v2",
            split=DatasetSplit.TEST,
            split_seed=7,
        )
    ]

    written = upsert_claim_label_rows(session, relabelled)

    stored = _stored(session)
    assert written == 1
    assert session.scalar(select(func.count()).select_from(ClaimSampleLabel)) == 2
    assert stored[DENIED_CLAIM].id == first_id
    assert stored[DENIED_CLAIM].is_denied is False
    assert stored[DENIED_CLAIM].denial_reason_category is None
    assert stored[DENIED_CLAIM].label_rule_version == "v2"
    assert stored[DENIED_CLAIM].split is DatasetSplit.TEST
    assert stored[DENIED_CLAIM].split_seed == 7
    assert stored[PAID_CLAIM].label_rule_version == "v1"  # not in the second batch, untouched


def test_upsert_sets_updated_at_when_a_row_is_stored_again(session: Session) -> None:
    _store_claims(session, PAID_CLAIM, DENIED_CLAIM)
    upsert_claim_label_rows(session, _rows())
    an_hour_ago = func.now() - func.make_interval(0, 0, 0, 0, 1)
    session.execute(update(ClaimSampleLabel).values(updated_at=an_hour_ago))
    before = _stored(session)[PAID_CLAIM].updated_at

    upsert_claim_label_rows(session, _rows())

    assert _stored(session)[PAID_CLAIM].updated_at > before


def test_upsert_of_no_rows_writes_nothing(session: Session) -> None:
    assert upsert_claim_label_rows(session, []) == 0


def test_upsert_rejects_a_label_whose_claim_is_not_stored(session: Session) -> None:
    _store_claims(session, PAID_CLAIM)

    with pytest.raises(IntegrityError, match="fk_claim_sample_labels_claim"):
        upsert_claim_label_rows(session, _rows())


def test_replace_leaves_only_the_given_rows(session: Session) -> None:
    _store_claims(session, PAID_CLAIM, DENIED_CLAIM)
    upsert_claim_label_rows(session, _rows())  # an earlier run: both claims, seed 42
    later_run = build_claim_label_rows([PAID_CLAIM], LINES[1:2], 7)

    written = replace_claim_label_rows(session, later_run)

    stored = _stored(session)
    assert written == 1
    assert set(stored) == {PAID_CLAIM}  # the other claim's label, made with seed 42, is gone
    assert stored[PAID_CLAIM].split_seed == 7


def test_replace_with_no_rows_empties_the_table(session: Session) -> None:
    _store_claims(session, PAID_CLAIM, DENIED_CLAIM)
    upsert_claim_label_rows(session, _rows())

    assert replace_claim_label_rows(session, []) == 0
    assert _stored(session) == {}


def test_replace_keeps_the_claims_themselves(session: Session) -> None:
    _store_claims(session, PAID_CLAIM, DENIED_CLAIM)
    upsert_claim_label_rows(session, _rows())

    replace_claim_label_rows(session, [])

    assert session.scalar(select(func.count()).select_from(ClaimSample)) == 2
