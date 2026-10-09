"""The amount-floor rule.

The rule reads the letter's total allowed charge, the only printed number that says how big a
claim is. The floor itself is assumed: no source gives it.
"""

from decimal import Decimal

from src.rules.verdict import RuleReason


def check_amount(total_allowed_charge_amount: Decimal | None, floor: Decimal) -> list[RuleReason]:
    """Return the amount reasons for one claim; an empty list means the claim is big enough.

    `floor` is always passed in. An amount exactly at the floor is big enough. Zero means the
    letter does not say what the claim was worth, so it is sent to review and is never reported
    as below the floor. A negative amount or floor is refused.
    """
    if floor < 0:
        raise ValueError("the amount floor must be zero or more")
    if total_allowed_charge_amount is None:
        return [RuleReason.AMOUNT_MISSING]
    if total_allowed_charge_amount < 0:
        raise ValueError("the total allowed charge must be zero or more")
    if total_allowed_charge_amount == 0:
        return [RuleReason.AMOUNT_ZERO]
    if total_allowed_charge_amount < floor:
        return [RuleReason.AMOUNT_BELOW_FLOOR]
    return []
