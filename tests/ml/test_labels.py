from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.db.models import DenialReasonCategory
from src.ml.labels import (
    COORDINATION_OF_BENEFITS_INDICATORS,
    LABEL_RULE_VERSION,
    ClaimLabel,
    appeal_success_chance,
    is_denied_line,
    label_claim,
)

CLAIM_ID = "800000000000001"


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


@pytest.mark.parametrize(
    "lines",
    [
        [PAID, _line(2, "A", "20.00", "30.00")],
        [PAID, _line(2, "C", "10.00", "30.00")],  # not allowed, but paid
        [PAID, _line(2, "A", "0.00")],  # allowed, but nothing paid
    ],
    ids=["all allowed and paid", "other indicator but paid", "allowed with zero payment"],
)
def test_claim_is_not_denied_unless_a_line_has_another_indicator_and_zero_payment(
    lines: list[Line],
) -> None:
    label = label_claim(CLAIM_ID, lines)

    assert label.is_denied is False
    assert label.denial_reason_category is None
    assert label.appeal_success_proxy is None


@pytest.mark.parametrize(
    ("line", "denied"),
    [
        (_line(1, "C", "0.00"), True),
        (_line(1, "C", "10.00", "30.00"), False),
        (_line(1, "A", "0.00"), False),
        (PAID, False),
    ],
    ids=[
        "other indicator, nothing paid",
        "other indicator but paid",
        "allowed, nothing paid",
        "paid",
    ],
)
def test_a_line_is_denied_only_with_another_indicator_and_zero_payment(
    line: Line, denied: bool
) -> None:
    assert is_denied_line(line) is denied


def test_claim_is_denied_when_any_one_line_is_denied() -> None:
    label = label_claim(CLAIM_ID, [PAID, _line(2, "C", "0.00"), _line(3, "A", "10.00", "10.00")])

    assert label.is_denied is True
    assert label.denial_reason_category is DenialReasonCategory.NONCOVERED
    assert label.appeal_success_proxy is not None


def test_label_carries_the_claim_id_and_the_rule_version() -> None:
    denied = label_claim(CLAIM_ID, [_line(1, "C", "0.00")])
    not_denied = label_claim(CLAIM_ID, [PAID])

    assert LABEL_RULE_VERSION == "v1"
    assert denied.source_claim_id == not_denied.source_claim_id == CLAIM_ID
    assert denied.label_rule_version == not_denied.label_rule_version == "v1"


@pytest.mark.parametrize(
    ("indicator", "category"),
    [
        ("C", DenialReasonCategory.NONCOVERED),
        ("N", DenialReasonCategory.MEDICAL_NECESSITY),
        ("M", DenialReasonCategory.DUPLICATE),
        ("B", DenialReasonCategory.BENEFITS_EXHAUSTED),
        ("S", DenialReasonCategory.COORDINATION_OF_BENEFITS),
        ("X", DenialReasonCategory.COORDINATION_OF_BENEFITS),
        ("<", DenialReasonCategory.COORDINATION_OF_BENEFITS),
        ("@", DenialReasonCategory.COORDINATION_OF_BENEFITS),
        ("O", DenialReasonCategory.OTHER),
        ("L", DenialReasonCategory.OTHER),
        ("R", DenialReasonCategory.OTHER),
        ("Z", DenialReasonCategory.OTHER),
        ("H", DenialReasonCategory.OTHER),  # not in the CMS codebook
        ("2", DenialReasonCategory.OTHER),  # not in the CMS codebook
    ],
)
def test_reason_category_comes_from_the_denied_lines_indicator(
    indicator: str, category: DenialReasonCategory
) -> None:
    label = label_claim(CLAIM_ID, [_line(1, indicator, "0.00")])

    assert label.denial_reason_category is category


def test_every_secondary_payer_code_of_the_codebook_is_coordination_of_benefits() -> None:
    # S, the lettered "MSP cost avoided" codes, and their twelve one-character symbol codes.
    assert frozenset("SQTUVXY!@#$*()+<>%&") == COORDINATION_OF_BENEFITS_INDICATORS
    for indicator in COORDINATION_OF_BENEFITS_INDICATORS:
        label = label_claim(CLAIM_ID, [_line(1, indicator, "0.00")])
        assert label.denial_reason_category is DenialReasonCategory.COORDINATION_OF_BENEFITS


def test_reason_category_comes_from_the_first_denied_line_whatever_the_order() -> None:
    lines = [_line(3, "N", "0.00"), PAID, _line(2, "M", "0.00"), _line(4, "C", "0.00")]

    label = label_claim(CLAIM_ID, lines)

    assert label.denial_reason_category is DenialReasonCategory.DUPLICATE


def test_a_paid_line_with_another_indicator_does_not_set_the_category() -> None:
    lines = [_line(1, "N", "30.00", "30.00"), _line(2, "C", "0.00")]

    label = label_claim(CLAIM_ID, lines)

    assert label.denial_reason_category is DenialReasonCategory.NONCOVERED


@pytest.mark.parametrize(
    ("category", "base"),
    [
        (DenialReasonCategory.MEDICAL_NECESSITY, "0.60"),
        (DenialReasonCategory.OTHER, "0.45"),
        (DenialReasonCategory.NONCOVERED, "0.25"),
        (DenialReasonCategory.COORDINATION_OF_BENEFITS, "0.20"),
        (DenialReasonCategory.DUPLICATE, "0.10"),
        (DenialReasonCategory.BENEFITS_EXHAUSTED, "0.10"),
    ],
)
def test_chance_in_the_low_band_is_the_categorys_base_chance(
    category: DenialReasonCategory, base: str
) -> None:
    assert appeal_success_chance(category, Decimal("50.00")) == Decimal(base)


@pytest.mark.parametrize(
    ("total", "chance"),
    [
        ("0.00", "0.35"),
        ("0.01", "0.45"),
        ("99.99", "0.45"),
        ("100.00", "0.50"),
        ("249.99", "0.50"),
        ("250.00", "0.55"),
        ("6740.00", "0.55"),
    ],
)
def test_chance_is_nudged_by_the_band_of_the_total_allowed_charge(total: str, chance: str) -> None:
    assert appeal_success_chance(DenialReasonCategory.OTHER, Decimal(total)) == Decimal(chance)


def test_chance_refuses_a_negative_total() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        appeal_success_chance(DenialReasonCategory.OTHER, Decimal("-0.01"))


# The draw for a claim id is fixed by the hash recipe (SHA-256 of "v1:<claim id>", first 8
# bytes, big-endian). These ids were worked out once; a change to the recipe moves them.
@pytest.mark.parametrize(
    ("claim_id", "expected"),
    [
        ("800000000000001", True),  # draw 0.070
        ("800000000000005", True),  # draw 0.357
        ("800000000000006", False),  # draw 0.471
        ("800000000000002", False),  # draw 0.508
    ],
)
def test_proxy_is_true_when_the_claims_draw_is_below_the_chance(
    claim_id: str, expected: bool
) -> None:
    label = label_claim(claim_id, [_line(1, "O", "0.00", "50.00")])  # chance 0.45

    assert label.appeal_success_proxy is expected


def test_proxy_uses_the_claims_total_allowed_charge_not_the_denied_lines() -> None:
    # Claim 800000000000002 draws 0.508: false at chance 0.45, true at 0.55 (high band).
    claim_id = "800000000000002"
    denied_line = _line(2, "O", "0.00", "0.00")

    alone = label_claim(claim_id, [_line(1, "A", "40.00", "50.00"), denied_line])
    with_large_paid_line = label_claim(claim_id, [_line(1, "A", "200.00", "250.00"), denied_line])

    assert alone.appeal_success_proxy is False
    assert with_large_paid_line.appeal_success_proxy is True


def test_same_claim_always_gets_the_same_label() -> None:
    lines = [PAID, _line(2, "N", "0.00")]

    assert label_claim(CLAIM_ID, lines) == label_claim(CLAIM_ID, list(reversed(lines)))


def test_proxy_is_never_true_when_the_chance_is_zero() -> None:
    # Duplicate (0.10) with a total allowed charge of zero (-0.10) gives a chance of 0.
    labels = [label_claim(f"{800000000000000 + n}", [_line(1, "M", "0.00")]) for n in range(500)]

    assert not any(label.appeal_success_proxy for label in labels)


def test_share_of_true_proxies_is_close_to_the_chance() -> None:
    lines = [_line(1, "O", "0.00", "50.00")]  # chance 0.45
    labels = [label_claim(f"{900000000000000 + n}", lines) for n in range(2000)]

    share = sum(label.appeal_success_proxy is True for label in labels) / len(labels)

    assert 0.40 < share < 0.50


def test_rejects_a_claim_without_lines() -> None:
    with pytest.raises(ValueError, match="has no lines"):
        label_claim(CLAIM_ID, [])


def test_rejects_a_claim_id_that_is_not_fifteen_digits() -> None:
    with pytest.raises(ValidationError, match="source_claim_id"):
        label_claim("8000", [PAID])


@pytest.mark.parametrize(
    ("is_denied", "category", "proxy"),
    [
        (True, None, True),
        (True, DenialReasonCategory.OTHER, None),
        (False, DenialReasonCategory.OTHER, None),
        (False, None, False),
    ],
)
def test_label_model_rejects_fields_that_do_not_match_is_denied(
    is_denied: bool, category: DenialReasonCategory | None, proxy: bool | None
) -> None:
    with pytest.raises(ValidationError, match="a denied claim needs"):
        ClaimLabel(
            source_claim_id=CLAIM_ID,
            is_denied=is_denied,
            denial_reason_category=category,
            appeal_success_proxy=proxy,
            label_rule_version="v1",
        )
