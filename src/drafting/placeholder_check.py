"""Finds placeholder text in a draft: text in square brackets, such as `[Your Name]`.

A model that lacks a value sometimes writes a blank for a person to fill in. Such a letter
is not ready for review, so the drafting function refuses it. The check is pure: no model
call and no database.

Only the paragraph text is looked at. A quote is copied evidence text, and the citation
validator already compares it with its chunk.
"""

import re

from src.drafting.schemas import AppealDraft, AppealLetter, DraftParagraph, LetterParagraph

# An opening bracket with a closing one after it, whatever is between them, also nothing.
PLACEHOLDER_PATTERN = re.compile(r"\[[^\]]*\]")


def find_placeholder_paragraphs(draft: AppealDraft | AppealLetter) -> tuple[int, ...]:
    """Return the positions, counted from 0, of the paragraphs with text in square brackets.

    `draft` is the model's answer or a checked letter (also one a person edited).
    """
    paragraphs: tuple[DraftParagraph | LetterParagraph, ...] = draft.paragraphs
    return tuple(
        index
        for index, paragraph in enumerate(paragraphs)
        if PLACEHOLDER_PATTERN.search(paragraph.text) is not None
    )
