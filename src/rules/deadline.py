"""The appeal deadline rule.

The rule reads the deadline as it is printed on the letter and never computes one: the appeal
window on the generated letters is made up (`docs/02-data.md` 2.4.1), so no real law is used.
"""

from datetime import date

from src.rules.verdict import RuleReason


def check_deadline(
    letter_date: date | None, appeal_deadline: date | None, today: date
) -> list[RuleReason]:
    """Return the deadline reasons for one claim; an empty list means the appeal is still open.

    `today` is always passed in. On the deadline day itself the appeal is still open. A
    deadline on or before the letter date looks wrong, so it is sent to review and is never
    reported as passed. A missing letter date leaves that check out.
    """
    if appeal_deadline is None:
        return [RuleReason.DEADLINE_MISSING]
    if letter_date is not None and appeal_deadline <= letter_date:
        return [RuleReason.DEADLINE_NOT_AFTER_LETTER_DATE]
    if today > appeal_deadline:
        return [RuleReason.DEADLINE_PASSED]
    return []
