"""Finds two wrong facts in a draft: a dollar amount no denied line has, and "other" as the reason.

A model sometimes gives a claim total as the amount of a service line, or copies the
category value `other` into the letter as if it were the denial's reason. Such a letter
states a wrong fact, so the drafting function refuses it. The check is pure: no model call
and no database.

Only the paragraph text is looked at. A quote is copied evidence text, and the citation
validator already compares it with its chunk.

Known limits: an amount written without a dollar sign is not seen; the sum of several denied
lines is refused, because it is no line's own amount; "other" as the reason is found by a
fixed list of wordings, and another wording passes.
"""

import re
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.drafting.schemas import AppealDraft
from src.extraction.schemas import DenialLetterExtraction

# A dollar sign and a number: `$70`, `$70.00`, `$1,250.00`. The group is the number.
DOLLAR_AMOUNT_PATTERN = re.compile(r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)")
THOUSANDS_SEPARATOR = ","
# Straight and curly quotation marks, single and double.
_QUOTATION_MARKS = "\"'‘’“”"
# The wordings that give the category value `other` as the denial's reason.
OTHER_AS_REASON_PATTERNS = (
    # The word alone in quotation marks, also with a comma or a full stop inside them.
    re.compile(rf"[{_QUOTATION_MARKS}]other[.,]?[{_QUOTATION_MARKS}]", re.IGNORECASE),
    # "the category of other", "the reason was other": the word ends the phrase, so
    # "a reason other than this one" and "the reason is other coverage" are not meant.
    re.compile(
        r"\b(?:category|categorized|classified|reason)\b(?:\s+\w+){0,3}?\s+(?:of|as|is|was)"
        r"\s+other\b(?=\s*(?:[.,;:!?)]|$))",
        re.IGNORECASE,
    ),
)


class FactProblemKind(StrEnum):
    AMOUNT_NOT_A_DENIED_LINE = "amount_not_a_denied_line"
    OTHER_AS_REASON = "other_as_reason"


class FactProblem(BaseModel):
    """One wrong fact: its kind and the paragraph it is in, counted from 0. Never any text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: FactProblemKind
    paragraph_index: int = Field(ge=0)


def find_fact_problems(
    draft: AppealDraft, letter: DenialLetterExtraction
) -> tuple[FactProblem, ...]:
    """Return the wrong facts in `draft`, in paragraph order; an empty tuple means none.

    `letter` is the denial letter the draft was written from. A dollar amount must equal the
    allowed or the paid amount of one of its denied lines, by value: `$70` equals `70.00`.
    A paragraph gives at most one problem of each kind.
    """
    line_amounts = _denied_line_amounts(letter)
    problems: list[FactProblem] = []
    for paragraph_index, paragraph in enumerate(draft.paragraphs):
        if any(amount not in line_amounts for amount in _dollar_amounts(paragraph.text)):
            problems.append(
                FactProblem(
                    kind=FactProblemKind.AMOUNT_NOT_A_DENIED_LINE,
                    paragraph_index=paragraph_index,
                )
            )
        if any(pattern.search(paragraph.text) for pattern in OTHER_AS_REASON_PATTERNS):
            problems.append(
                FactProblem(kind=FactProblemKind.OTHER_AS_REASON, paragraph_index=paragraph_index)
            )
    return tuple(problems)


def _denied_line_amounts(letter: DenialLetterExtraction) -> frozenset[Decimal]:
    # No denied lines (the field is missing or empty) gives no amount a draft may name.
    lines = letter.denied_lines.value or ()
    return frozenset(
        amount for line in lines for amount in (line.allowed_charge_amount, line.payment_amount)
    )


def _dollar_amounts(text: str) -> list[Decimal]:
    return [
        Decimal(number.replace(THOUSANDS_SEPARATOR, ""))
        for number in DOLLAR_AMOUNT_PATTERN.findall(text)
    ]
