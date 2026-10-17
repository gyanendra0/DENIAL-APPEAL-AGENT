"""Checks that a person's edit of a letter only changes what an edit may change.

An edit may change a paragraph's text, remove a paragraph, and remove a citation. It may not
change a paragraph's kind, add or move a paragraph, or add or change a citation: a new quote
would need a new search, which is a model call.

The edit is always compared with the model's draft, not with the version before it, so a
paragraph or a citation removed in one edit may come back in a later one. The check is pure:
no model call, no database, no clock.
"""

from src.drafting.letter_check import LetterProblem, LetterProblemKind
from src.drafting.schemas import AppealLetter, Citation, LetterParagraph


def find_edit_problems(original: AppealLetter, edited: AppealLetter) -> tuple[LetterProblem, ...]:
    """Return a problem for each paragraph of `edited` that no edit of `original` can give.

    Each edited paragraph, in order, must line up with a later paragraph of `original` of the
    same kind whose citations hold its citations, unchanged and in the same order. Lining up
    each one with the first such paragraph finds a way whenever there is one.
    """
    problems: list[LetterProblem] = []
    next_original = 0
    for paragraph_index, paragraph in enumerate(edited.paragraphs):
        position = next_original
        while position < len(original.paragraphs) and not _may_come_from(
            original.paragraphs[position], paragraph
        ):
            position += 1
        if position == len(original.paragraphs):
            problems.append(
                LetterProblem(
                    kind=LetterProblemKind.EDIT_NOT_ALLOWED, paragraph_index=paragraph_index
                )
            )
            continue
        next_original = position + 1
    return tuple(problems)


def _may_come_from(original: LetterParagraph, edited: LetterParagraph) -> bool:
    return original.kind is edited.kind and _is_in_order_within(
        edited.citations, original.citations
    )


def _is_in_order_within(part: tuple[Citation, ...], whole: tuple[Citation, ...]) -> bool:
    remaining = iter(whole)
    return all(any(citation == candidate for candidate in remaining) for citation in part)
