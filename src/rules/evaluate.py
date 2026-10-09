"""The one entry point of the deterministic rules: every rule, one verdict.

Callers use `evaluate_rules` and not the single rules, so a claim always gets every check and
the outcome always fits the reasons.
"""

from datetime import date
from decimal import Decimal

from src.rules.amount import check_amount
from src.rules.deadline import check_deadline
from src.rules.verdict import RuleInputs, RuleOutcome, RuleReason, RuleVerdict

# The reasons that stop a claim. Every other reason sends it to a person.
HARD_FAIL_REASONS = frozenset({RuleReason.DEADLINE_PASSED, RuleReason.AMOUNT_BELOW_FLOOR})


def evaluate_rules(inputs: RuleInputs, *, today: date, amount_floor: Decimal) -> RuleVerdict:
    """Run every rule on one claim's letter values and return the combined verdict.

    `today` and `amount_floor` are always passed in. Every rule runs, so the verdict lists
    every reason that fired, in `RuleReason` order, and not only the first. One hard-fail
    reason makes the outcome `hard_fail` whatever else fired; otherwise any reason makes it
    `must_review`; no reason means `pass`. A floor that is not an exact decimal of zero or
    more is refused.
    """
    reasons = (
        *check_deadline(inputs.letter_date, inputs.appeal_deadline, today),
        *check_amount(inputs.total_allowed_charge_amount, amount_floor),
    )
    return RuleVerdict(outcome=_outcome_for(reasons), reasons=reasons)


def _outcome_for(reasons: tuple[RuleReason, ...]) -> RuleOutcome:
    if any(reason in HARD_FAIL_REASONS for reason in reasons):
        return RuleOutcome.HARD_FAIL
    if reasons:
        return RuleOutcome.MUST_REVIEW
    return RuleOutcome.PASS
