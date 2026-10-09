from decimal import Decimal

import pytest

from src.rules.amount import check_amount
from src.rules.verdict import RuleReason

FLOOR = Decimal("25")


@pytest.mark.parametrize(
    "amount",
    [Decimal("0.01"), Decimal("10.00"), Decimal("24.99")],
    ids=["one cent", "the smallest real amount", "one cent below the floor"],
)
def test_an_amount_above_zero_and_below_the_floor_is_below_the_floor(amount: Decimal) -> None:
    assert check_amount(amount, FLOOR) == [RuleReason.AMOUNT_BELOW_FLOOR]


@pytest.mark.parametrize(
    "amount",
    [Decimal("25"), Decimal("25.00"), Decimal("25.01"), Decimal("3440.00")],
    ids=["at the floor", "at the floor with cents", "one cent above", "far above"],
)
def test_an_amount_at_or_above_the_floor_is_big_enough(amount: Decimal) -> None:
    assert check_amount(amount, FLOOR) == []


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("0.00")], ids=["0", "0.00"])
def test_an_amount_of_zero_is_reported_as_zero_and_not_as_below_the_floor(amount: Decimal) -> None:
    assert check_amount(amount, FLOOR) == [RuleReason.AMOUNT_ZERO]


@pytest.mark.parametrize("floor", [Decimal("0"), FLOOR, Decimal("50")], ids=["0", "25", "50"])
def test_a_missing_amount_is_reported_as_missing_whatever_the_floor_is(floor: Decimal) -> None:
    assert check_amount(None, floor) == [RuleReason.AMOUNT_MISSING]


def test_a_floor_of_zero_stops_no_amount() -> None:
    assert check_amount(Decimal("0.01"), Decimal("0")) == []
    assert check_amount(Decimal("0"), Decimal("0")) == [RuleReason.AMOUNT_ZERO]


def test_the_floor_passed_in_is_the_one_used() -> None:
    amount = Decimal("30.00")

    assert check_amount(amount, FLOOR) == []
    assert check_amount(amount, Decimal("50")) == [RuleReason.AMOUNT_BELOW_FLOOR]


def test_a_negative_floor_is_refused() -> None:
    with pytest.raises(ValueError, match="floor"):
        check_amount(Decimal("90.00"), Decimal("-1"))


def test_a_negative_floor_is_refused_even_when_the_amount_is_missing() -> None:
    with pytest.raises(ValueError, match="floor"):
        check_amount(None, Decimal("-1"))


@pytest.mark.parametrize(
    "floor",
    [25.0, 25, "25", Decimal("NaN"), Decimal("Infinity")],
    ids=["a float", "a whole number", "text", "not a number", "infinite"],
)
def test_a_floor_that_is_not_an_exact_finite_decimal_is_refused(floor: object) -> None:
    with pytest.raises(ValueError, match="floor"):
        check_amount(Decimal("24.99"), floor)  # type: ignore[arg-type]


def test_a_negative_amount_is_refused_and_not_reported_as_below_the_floor() -> None:
    with pytest.raises(ValueError, match="total allowed charge"):
        check_amount(Decimal("-0.01"), FLOOR)


def test_the_floor_has_no_default() -> None:
    with pytest.raises(TypeError, match="floor"):
        check_amount(Decimal("90.00"))  # type: ignore[call-arg]
