from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.db.models import DenialReasonCategory
from src.ml.features import FEATURE_VERSION, ClaimFeatures, build_claim_features
from src.ml.labels import appeal_success_chance, claim_appeal_success_chance, label_claim


@dataclass(frozen=True)
class Line:
    """A made-up service line with the four fields the rule reads."""

    line_number: int
    processing_indicator: str
    payment_amount: Decimal
    allowed_charge_amount: Decimal


def _line(number: int, indicator: str, payment: str, allowed: str = "0.00") -> Line:
    return Line(number, indicator, Decimal(payment), Decimal(allowed))


PAID = _line(1, "A", "40.00", "50.00")
DENIED = _line(2, "N", "0.00", "70.00")


def test_the_feature_version_is_v1() -> None:
    assert FEATURE_VERSION == "v1"


def test_the_category_is_the_one_passed_in() -> None:
    features = build_claim_features(DenialReasonCategory.NONCOVERED, [PAID, DENIED])

    assert features.denial_reason_category is DenialReasonCategory.NONCOVERED


def test_the_total_allowed_charge_counts_paid_lines_too() -> None:
    features = build_claim_features(DenialReasonCategory.MEDICAL_NECESSITY, [PAID, DENIED])

    assert features.total_allowed_charge == Decimal("120.00")


def test_a_claim_is_fully_denied_when_every_line_is_denied() -> None:
    lines = [DENIED, _line(3, "C", "0.00", "30.00")]

    assert build_claim_features(DenialReasonCategory.MEDICAL_NECESSITY, lines).fully_denied is True


@pytest.mark.parametrize(
    "other_line",
    [
        PAID,
        _line(3, "C", "10.00", "30.00"),  # not allowed, but paid
        _line(3, "A", "0.00"),  # allowed, but nothing paid
    ],
    ids=["allowed and paid", "other indicator but paid", "allowed with zero payment"],
)
def test_a_claim_is_partly_denied_when_one_line_is_not_denied(other_line: Line) -> None:
    features = build_claim_features(DenialReasonCategory.MEDICAL_NECESSITY, [DENIED, other_line])

    assert features.fully_denied is False


def test_the_order_of_the_lines_does_not_change_the_features() -> None:
    category = DenialReasonCategory.MEDICAL_NECESSITY

    assert build_claim_features(category, [PAID, DENIED]) == build_claim_features(
        category, [DENIED, PAID]
    )


def test_no_feature_changes_when_the_proxy_flips() -> None:
    # An `other` claim, fully denied, low band: chance 0.45, so both proxy values turn up.
    lines = [_line(1, "Z", "0.00", "50.00")]
    labels = [label_claim(f"8000000000000{number:02d}", lines) for number in range(1, 51)]
    won = next(label for label in labels if label.appeal_success_proxy is True)
    lost = next(label for label in labels if label.appeal_success_proxy is False)
    assert won.denial_reason_category is not None
    assert lost.denial_reason_category is not None

    assert build_claim_features(won.denial_reason_category, lines) == build_claim_features(
        lost.denial_reason_category, lines
    )


def test_the_feature_row_has_the_three_rule_inputs_and_nothing_else() -> None:
    # No claim id, no proxy, no split, no chance.
    assert set(ClaimFeatures.model_fields) == {
        "denial_reason_category",
        "total_allowed_charge",
        "fully_denied",
    }


@pytest.mark.parametrize(
    "lines",
    [
        [_line(1, "N", "0.00")],
        [_line(1, "N", "0.00", "50.00")],
        [PAID, _line(2, "C", "0.00", "70.00")],
        [_line(1, "M", "0.00", "100.00"), _line(2, "B", "0.00", "150.00")],
        [PAID, _line(2, "Z", "0.00", "400.00"), _line(3, "A", "0.00")],
    ],
    ids=["zero band", "low band", "mid band partly", "high band fully", "high band partly"],
)
def test_the_features_are_the_inputs_the_label_rule_draws_against(lines: list[Line]) -> None:
    label = label_claim("800000000000001", lines)
    assert label.denial_reason_category is not None

    features = build_claim_features(label.denial_reason_category, lines)

    assert appeal_success_chance(
        features.denial_reason_category,
        features.total_allowed_charge,
        fully_denied=features.fully_denied,
    ) == claim_appeal_success_chance(lines)


def test_a_claim_with_no_lines_is_rejected() -> None:
    with pytest.raises(ValueError, match="no lines"):
        build_claim_features(DenialReasonCategory.OTHER, [])


def test_a_claim_with_no_denied_line_is_rejected() -> None:
    with pytest.raises(ValueError, match="no denied line"):
        build_claim_features(DenialReasonCategory.OTHER, [PAID])


def test_a_feature_row_cannot_be_changed() -> None:
    features = build_claim_features(DenialReasonCategory.OTHER, [DENIED])

    with pytest.raises(ValidationError):
        features.fully_denied = False


def test_a_negative_total_allowed_charge_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ClaimFeatures(
            denial_reason_category=DenialReasonCategory.OTHER,
            total_allowed_charge=Decimal("-0.01"),
            fully_denied=True,
        )
