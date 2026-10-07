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

    assert LABEL_RULE_VERSION == "v2"
    assert denied.source_claim_id == not_denied.source_claim_id == CLAIM_ID
    assert denied.label_rule_version == not_denied.label_rule_version == "v2"


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
    ("category", "chance"),
    [
        (DenialReasonCategory.MEDICAL_NECESSITY, "0.90"),  # base 0.75
        (DenialReasonCategory.OTHER, "0.70"),  # base 0.55
        (DenialReasonCategory.NONCOVERED, "0.35"),  # base 0.20
        (DenialReasonCategory.COORDINATION_OF_BENEFITS, "0.30"),  # base 0.15
        (DenialReasonCategory.DUPLICATE, "0.20"),  # base 0.05
        (DenialReasonCategory.BENEFITS_EXHAUSTED, "0.20"),  # base 0.05
    ],
)
def test_chance_of_a_partly_denied_claim_in_the_low_band_is_the_base_chance_plus_015(
    category: DenialReasonCategory, chance: str
) -> None:
    assert appeal_success_chance(category, Decimal("50.00"), fully_denied=False) == Decimal(chance)


@pytest.mark.parametrize(
    ("total", "chance"),
    [
        ("0.00", "0.55"),
        ("0.01", "0.70"),
        ("99.99", "0.70"),
        ("100.00", "0.80"),
        ("249.99", "0.80"),
        ("250.00", "0.90"),
        ("6740.00", "0.90"),
    ],
)
def test_chance_is_nudged_by_the_band_of_the_total_allowed_charge(total: str, chance: str) -> None:
    # Partly denied `other`: 0.55 + 0.15 = 0.70 before the band nudge.
    assert appeal_success_chance(
        DenialReasonCategory.OTHER, Decimal(total), fully_denied=False
    ) == Decimal(chance)


def test_chance_is_higher_for_a_partly_denied_claim_than_for_a_fully_denied_one() -> None:
    category, total = DenialReasonCategory.OTHER, Decimal("50.00")

    assert appeal_success_chance(category, total, fully_denied=False) == Decimal("0.70")
    assert appeal_success_chance(category, total, fully_denied=True) == Decimal("0.45")


def test_chance_of_the_design_notes_worked_example() -> None:
    # Partly denied `noncovered`, total 120.00 (mid band): 0.20 + 0.10 + 0.15.
    chance = appeal_success_chance(
        DenialReasonCategory.NONCOVERED, Decimal("120.00"), fully_denied=False
    )

    assert chance == Decimal("0.45")


@pytest.mark.parametrize(
    ("category", "total", "fully_denied", "chance"),
    [
        (DenialReasonCategory.DUPLICATE, "0.00", True, "0.02"),  # sum -0.20
        (DenialReasonCategory.DUPLICATE, "50.00", True, "0.02"),  # sum -0.05
        (DenialReasonCategory.MEDICAL_NECESSITY, "250.00", False, "0.98"),  # sum 1.10
        (DenialReasonCategory.MEDICAL_NECESSITY, "100.00", False, "0.98"),  # sum 1.00
    ],
)
def test_chance_never_leaves_the_lowest_and_highest_chance_allowed(
    category: DenialReasonCategory, total: str, fully_denied: bool, chance: str
) -> None:
    assert appeal_success_chance(category, Decimal(total), fully_denied=fully_denied) == Decimal(
        chance
    )


def test_chance_refuses_a_negative_total() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        appeal_success_chance(DenialReasonCategory.OTHER, Decimal("-0.01"), fully_denied=False)


# The draw for a claim id is fixed by the hash recipe (SHA-256 of "v2:<claim id>", first 8
# bytes, big-endian). These ids were worked out once; a change to the recipe moves them.
@pytest.mark.parametrize(
    ("claim_id", "expected"),
    [
        ("800000000000008", True),  # draw 0.153
        ("800000000000006", True),  # draw 0.409
        ("800000000000005", False),  # draw 0.468
        ("800000000000001", False),  # draw 0.669
    ],
)
def test_proxy_is_true_when_the_claims_draw_is_below_the_chance(
    claim_id: str, expected: bool
) -> None:
    # Fully denied `other` in the low band: chance 0.55 - 0.10 = 0.45.
    label = label_claim(claim_id, [_line(1, "O", "0.00", "50.00")])

    assert label.appeal_success_proxy is expected


def test_proxy_uses_the_claims_total_allowed_charge_not_the_denied_lines() -> None:
    # Claim 800000000000002 draws 0.841: false at chance 0.70, true at 0.90 (high band).
    claim_id = "800000000000002"
    denied_line = _line(2, "O", "0.00", "0.00")

    alone = label_claim(claim_id, [_line(1, "A", "40.00", "50.00"), denied_line])
    with_large_paid_line = label_claim(claim_id, [_line(1, "A", "200.00", "250.00"), denied_line])

    assert alone.appeal_success_proxy is False
    assert with_large_paid_line.appeal_success_proxy is True


@pytest.mark.parametrize(
    "other_line",
    [_line(1, "A", "40.00", "50.00"), _line(1, "N", "40.00", "50.00")],
    ids=["allowed and paid", "other indicator but paid"],
)
def test_a_claim_is_fully_denied_only_when_every_line_is_denied(other_line: Line) -> None:
    # Claim 800000000000001 draws 0.669; `other` in the low band: false when fully denied
    # (chance 0.45), true when partly denied (chance 0.70). The total is 50.00 in each claim.
    fully = label_claim(CLAIM_ID, [_line(1, "O", "0.00", "20.00"), _line(2, "O", "0.00", "30.00")])
    partly = label_claim(CLAIM_ID, [other_line, _line(2, "O", "0.00")])

    assert fully.appeal_success_proxy is False
    assert partly.appeal_success_proxy is True


def test_same_claim_always_gets_the_same_label() -> None:
    lines = [PAID, _line(2, "N", "0.00")]

    assert label_claim(CLAIM_ID, lines) == label_claim(CLAIM_ID, list(reversed(lines)))


def test_proxy_is_rarely_but_sometimes_true_at_the_lowest_chance() -> None:
    # Fully denied duplicate with a total allowed charge of zero: the sum is -0.20, kept at 0.02.
    labels = [label_claim(f"{800000000000000 + n}", [_line(1, "M", "0.00")]) for n in range(2000)]

    share = sum(label.appeal_success_proxy is True for label in labels) / len(labels)

    assert 0 < share < 0.05


def test_share_of_true_proxies_is_close_to_the_chance() -> None:
    lines = [_line(1, "O", "0.00", "50.00")]  # fully denied, chance 0.45
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
            label_rule_version="v2",
        )
