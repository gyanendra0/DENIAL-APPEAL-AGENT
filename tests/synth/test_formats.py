from datetime import date

import pytest

from src.synth.formats import (
    CODE_NOT_PROVIDED,
    MONTH_NAMES,
    iso_date,
    long_date,
    service_dates,
    us_date,
)

DAY = date(2009, 3, 3)


def test_long_date_spells_the_month_and_does_not_pad_the_day() -> None:
    assert long_date(DAY) == "March 3, 2009"
    assert long_date(date(2010, 12, 31)) == "December 31, 2010"


def test_us_date_puts_the_month_first_and_pads_both_parts() -> None:
    assert us_date(DAY) == "03/03/2009"
    assert us_date(date(2010, 12, 1)) == "12/01/2010"


def test_iso_date_puts_the_year_first() -> None:
    assert iso_date(DAY) == "2009-03-03"


def test_every_month_has_its_own_name() -> None:
    names = [long_date(date(2009, month, 1)).split()[0] for month in range(1, 13)]

    assert tuple(names) == MONTH_NAMES
    assert len(set(names)) == 12


@pytest.mark.parametrize("show", [long_date, us_date, iso_date])
def test_service_dates_is_one_date_when_both_are_equal(show: object) -> None:
    assert callable(show)
    assert service_dates(DAY, DAY, show) == show(DAY)


def test_service_dates_joins_two_different_dates_with_to() -> None:
    assert service_dates(DAY, date(2009, 3, 5), iso_date) == "2009-03-03 to 2009-03-05"


def test_the_wording_for_a_missing_code_is_fixed() -> None:
    # Every document type prints this text; changing it changes all of them.
    assert CODE_NOT_PROVIDED == "not provided"
