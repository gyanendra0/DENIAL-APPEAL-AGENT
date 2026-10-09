import inspect
from datetime import date
from decimal import Decimal

import pytest

from src.rules.evaluate import HARD_FAIL_REASONS, evaluate_rules
from src.rules.verdict import RuleInputs, RuleOutcome, RuleReason, RuleVerdict

TODAY = date(2010, 1, 1)
FLOOR = Decimal("25")

LETTER_DATE = date(2009, 7, 15)
OPEN_DEADLINE = date(2010, 1, 11)
PASSED_DEADLINE = date(2009, 12, 31)
TOO_EARLY_DEADLINE = date(2009, 7, 15)

BIG_AMOUNT = Decimal("90.00")
SMALL_AMOUNT = Decimal("10.00")
ZERO_AMOUNT = Decimal("0.00")

# Each deadline value and each amount value with the reason it gives on its own.
DEADLINES: list[tuple[date | None, RuleReason | None]] = [
    (OPEN_DEADLINE, None),
    (TOO_EARLY_DEADLINE, RuleReason.DEADLINE_NOT_AFTER_LETTER_DATE),
    (None, RuleReason.DEADLINE_MISSING),
    (PASSED_DEADLINE, RuleReason.DEADLINE_PASSED),
]
AMOUNTS: list[tuple[Decimal | None, RuleReason | None]] = [
    (BIG_AMOUNT, None),
    (None, RuleReason.AMOUNT_MISSING),
    (ZERO_AMOUNT, RuleReason.AMOUNT_ZERO),
    (SMALL_AMOUNT, RuleReason.AMOUNT_BELOW_FLOOR),
]


def evaluate(
    appeal_deadline: date | None = OPEN_DEADLINE,
    amount: Decimal | None = BIG_AMOUNT,
    *,
    today: date = TODAY,
    amount_floor: Decimal = FLOOR,
) -> RuleVerdict:
    inputs = RuleInputs(
        letter_date=LETTER_DATE,
        appeal_deadline=appeal_deadline,
        total_allowed_charge_amount=amount,
    )
    return evaluate_rules(inputs, today=today, amount_floor=amount_floor)


def test_a_claim_no_rule_stops_passes_with_no_reason() -> None:
    verdict = evaluate()

    assert verdict.outcome is RuleOutcome.PASS
    assert verdict.reasons == ()


def test_the_hard_fail_reasons_are_the_passed_deadline_and_the_small_amount() -> None:
    assert (
        frozenset({RuleReason.DEADLINE_PASSED, RuleReason.AMOUNT_BELOW_FLOOR}) == HARD_FAIL_REASONS
    )


@pytest.mark.parametrize(
    ("appeal_deadline", "amount", "reason"),
    [
        (PASSED_DEADLINE, BIG_AMOUNT, RuleReason.DEADLINE_PASSED),
        (OPEN_DEADLINE, SMALL_AMOUNT, RuleReason.AMOUNT_BELOW_FLOOR),
    ],
    ids=["deadline passed", "amount below the floor"],
)
def test_a_hard_fail_reason_on_its_own_stops_the_claim(
    appeal_deadline: date, amount: Decimal, reason: RuleReason
) -> None:
    verdict = evaluate(appeal_deadline, amount)

    assert verdict.outcome is RuleOutcome.HARD_FAIL
    assert verdict.reasons == (reason,)


@pytest.mark.parametrize(
    ("appeal_deadline", "amount", "reason"),
    [
        (TOO_EARLY_DEADLINE, BIG_AMOUNT, RuleReason.DEADLINE_NOT_AFTER_LETTER_DATE),
        (None, BIG_AMOUNT, RuleReason.DEADLINE_MISSING),
        (OPEN_DEADLINE, None, RuleReason.AMOUNT_MISSING),
        (OPEN_DEADLINE, ZERO_AMOUNT, RuleReason.AMOUNT_ZERO),
    ],
    ids=["deadline too early", "deadline missing", "amount missing", "amount zero"],
)
def test_a_review_reason_on_its_own_sends_the_claim_to_a_person(
    appeal_deadline: date | None, amount: Decimal | None, reason: RuleReason
) -> None:
    verdict = evaluate(appeal_deadline, amount)

    assert verdict.outcome is RuleOutcome.MUST_REVIEW
    assert verdict.reasons == (reason,)


def test_a_passed_deadline_wins_over_an_amount_that_needs_review() -> None:
    verdict = evaluate(PASSED_DEADLINE, ZERO_AMOUNT)

    assert verdict.outcome is RuleOutcome.HARD_FAIL
    assert verdict.reasons == (RuleReason.DEADLINE_PASSED, RuleReason.AMOUNT_ZERO)


def test_a_small_amount_wins_over_a_deadline_that_needs_review() -> None:
    verdict = evaluate(None, SMALL_AMOUNT)

    assert verdict.outcome is RuleOutcome.HARD_FAIL
    assert verdict.reasons == (RuleReason.DEADLINE_MISSING, RuleReason.AMOUNT_BELOW_FLOOR)


def test_two_hard_fail_reasons_are_both_listed() -> None:
    verdict = evaluate(PASSED_DEADLINE, SMALL_AMOUNT)

    assert verdict.outcome is RuleOutcome.HARD_FAIL
    assert verdict.reasons == (RuleReason.DEADLINE_PASSED, RuleReason.AMOUNT_BELOW_FLOOR)


def test_two_review_reasons_are_both_listed() -> None:
    verdict = evaluate(None, None)

    assert verdict.outcome is RuleOutcome.MUST_REVIEW
    assert verdict.reasons == (RuleReason.DEADLINE_MISSING, RuleReason.AMOUNT_MISSING)


@pytest.mark.parametrize(("appeal_deadline", "deadline_reason"), DEADLINES)
@pytest.mark.parametrize(("amount", "amount_reason"), AMOUNTS)
def test_every_combination_lists_its_reasons_in_order_with_a_fitting_outcome(
    appeal_deadline: date | None,
    deadline_reason: RuleReason | None,
    amount: Decimal | None,
    amount_reason: RuleReason | None,
) -> None:
    expected = tuple(reason for reason in (deadline_reason, amount_reason) if reason is not None)

    verdict = evaluate(appeal_deadline, amount)

    assert verdict.reasons == expected
    assert list(verdict.reasons) == sorted(verdict.reasons, key=list(RuleReason).index)
    if HARD_FAIL_REASONS & set(expected):
        assert verdict.outcome is RuleOutcome.HARD_FAIL
    elif expected:
        assert verdict.outcome is RuleOutcome.MUST_REVIEW
    else:
        assert verdict.outcome is RuleOutcome.PASS


def test_the_today_passed_in_is_the_one_used() -> None:
    on_the_deadline = evaluate(today=OPEN_DEADLINE)
    the_day_after = evaluate(today=date(2010, 1, 12))

    assert on_the_deadline.outcome is RuleOutcome.PASS
    assert the_day_after.reasons == (RuleReason.DEADLINE_PASSED,)


def test_the_floor_passed_in_is_the_one_used() -> None:
    amount = Decimal("30.00")

    assert evaluate(amount=amount, amount_floor=FLOOR).outcome is RuleOutcome.PASS
    assert evaluate(amount=amount, amount_floor=Decimal("50")).reasons == (
        RuleReason.AMOUNT_BELOW_FLOOR,
    )


def test_today_and_the_floor_have_no_default_and_must_be_named() -> None:
    parameters = inspect.signature(evaluate_rules).parameters

    for name in ("today", "amount_floor"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty


def test_a_negative_floor_is_refused() -> None:
    with pytest.raises(ValueError, match="floor"):
        evaluate(amount_floor=Decimal("-1"))


def test_the_verdict_carries_the_rules_version() -> None:
    assert evaluate().rules_version == "v1"
