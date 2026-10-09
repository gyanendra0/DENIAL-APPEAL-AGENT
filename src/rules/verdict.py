"""The types the deterministic rules share: what they read and what they answer.

The rules sort a denied claim with no model call: it may go on to an appeal draft (`pass`), a
person must look at it (`must_review`), or it is stopped (`hard_fail`). They never send
anything and never decide an appeal.

Nothing in `src/rules` reads the clock, the settings or an extraction result. The caller takes
the plain values out of the extraction and passes "today" and the amount floor in.

Any change to a rule, a reason or the order of the reasons needs a new `RULES_VERSION`.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

RULES_VERSION = "v1"


class RuleOutcome(StrEnum):
    PASS = "pass"  # noqa: S105  (an outcome name, not a password)
    MUST_REVIEW = "must_review"
    HARD_FAIL = "hard_fail"


class RuleReason(StrEnum):
    """Why a rule fired. The order here is the order of the reasons in a verdict."""

    DEADLINE_NOT_AFTER_LETTER_DATE = "deadline_not_after_letter_date"
    DEADLINE_MISSING = "deadline_missing"
    DEADLINE_PASSED = "deadline_passed"
    AMOUNT_MISSING = "amount_missing"
    AMOUNT_ZERO = "amount_zero"
    AMOUNT_BELOW_FLOOR = "amount_below_floor"


class RuleInputs(BaseModel):
    """The three values of one denial letter the rules read. `None` means the value is missing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    letter_date: date | None
    appeal_deadline: date | None
    total_allowed_charge_amount: Decimal | None = Field(ge=0, strict=True)


class RuleVerdict(BaseModel):
    """The one answer for a claim: the outcome and every reason that fired."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RuleOutcome
    reasons: tuple[RuleReason, ...]
    rules_version: str = RULES_VERSION
