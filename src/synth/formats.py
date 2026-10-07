"""How every generated document writes a date or a missing code, and the claim fields it reads.

The denial letter, the clinical note and the prior-authorisation record print the same claim in
different layouts. The three date styles, the "not provided" wording and the claim fields are
here so the generators cannot drift apart.

A change to a date style or to `CODE_NOT_PROVIDED` changes the text of every document type, so
each generator then needs a new version. The pinned hashes in the generator tests catch it.
"""

from collections.abc import Callable, Sequence
from datetime import date
from typing import Protocol

CODE_NOT_PROVIDED = "not provided"
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class DocumentClaim(Protocol):
    """The four fields of a claim that a generated document reads."""

    @property
    def source_claim_id(self) -> str: ...

    @property
    def claim_from_date(self) -> date: ...

    @property
    def claim_thru_date(self) -> date: ...

    @property
    def diagnosis_codes(self) -> Sequence[str]: ...


def long_date(day: date) -> str:
    """A date as `March 3, 2009`."""
    return f"{MONTH_NAMES[day.month - 1]} {day.day}, {day.year}"


def us_date(day: date) -> str:
    """A date as `03/03/2009`."""
    return f"{day.month:02d}/{day.day:02d}/{day.year}"


def iso_date(day: date) -> str:
    """A date as `2009-03-03`."""
    return day.isoformat()


def service_dates(first: date, last: date, show: Callable[[date], str]) -> str:
    """One date when the service began and ended on the same day, else `first to last`."""
    if first == last:
        return show(first)
    return f"{show(first)} to {show(last)}"
