from datetime import date

import pytest

from src.rules.deadline import check_deadline
from src.rules.verdict import RuleReason

LETTER_DATE = date(2009, 7, 15)
DEADLINE = date(2010, 1, 11)


@pytest.mark.parametrize(
    "today",
    [date(2009, 7, 16), date(2010, 1, 10), DEADLINE],
    ids=["long before", "the day before", "the deadline day itself"],
)
def test_an_appeal_is_still_open_up_to_and_on_the_deadline_day(today: date) -> None:
    assert check_deadline(LETTER_DATE, DEADLINE, today) == []


@pytest.mark.parametrize(
    "today", [date(2010, 1, 12), date(2026, 10, 10)], ids=["the day after", "years after"]
)
def test_a_deadline_before_today_has_passed(today: date) -> None:
    assert check_deadline(LETTER_DATE, DEADLINE, today) == [RuleReason.DEADLINE_PASSED]


@pytest.mark.parametrize(
    "today", [date(2009, 7, 16), date(2026, 10, 10)], ids=["early today", "late today"]
)
def test_a_missing_deadline_is_reported_as_missing_whatever_today_is(today: date) -> None:
    assert check_deadline(LETTER_DATE, None, today) == [RuleReason.DEADLINE_MISSING]


def test_a_missing_deadline_and_letter_date_is_reported_as_a_missing_deadline() -> None:
    assert check_deadline(None, None, date(2010, 1, 1)) == [RuleReason.DEADLINE_MISSING]


@pytest.mark.parametrize(
    "deadline",
    [LETTER_DATE, date(2009, 7, 14), date(1999, 7, 15)],
    ids=["on the letter date", "the day before it", "years before it"],
)
def test_a_deadline_on_or_before_the_letter_date_looks_wrong(deadline: date) -> None:
    today = date(2009, 7, 1)  # before every deadline here, so nothing has passed

    assert check_deadline(LETTER_DATE, deadline, today) == [
        RuleReason.DEADLINE_NOT_AFTER_LETTER_DATE
    ]


@pytest.mark.parametrize(
    "deadline", [LETTER_DATE, date(2009, 7, 14)], ids=["on the letter date", "before it"]
)
def test_a_deadline_that_looks_wrong_is_not_reported_as_passed(deadline: date) -> None:
    reasons = check_deadline(LETTER_DATE, deadline, date(2026, 10, 10))

    assert reasons == [RuleReason.DEADLINE_NOT_AFTER_LETTER_DATE]


def test_a_deadline_one_day_after_the_letter_date_is_checked_as_usual() -> None:
    deadline = date(2009, 7, 16)

    assert check_deadline(LETTER_DATE, deadline, deadline) == []
    assert check_deadline(LETTER_DATE, deadline, date(2009, 7, 17)) == [RuleReason.DEADLINE_PASSED]


def test_a_missing_letter_date_leaves_only_the_passed_check() -> None:
    assert check_deadline(None, DEADLINE, DEADLINE) == []
    assert check_deadline(None, DEADLINE, date(2010, 1, 12)) == [RuleReason.DEADLINE_PASSED]


def test_today_has_no_default() -> None:
    with pytest.raises(TypeError, match="today"):
        check_deadline(LETTER_DATE, DEADLINE)  # type: ignore[call-arg]
