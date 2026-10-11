"""The shape of a drafted appeal letter: paragraphs, and the citations of the policy ones.

A draft is a list of paragraphs. A paragraph that states policy is marked `policy` and names
its evidence by a short label (`E1` is the first retrieved match, `E2` the second) with an
exact quote from it. Every other paragraph is marked `other` and has no citation.

The model writes labels and quotes only. It never writes the fields that name a chunk
(`source`, `source_document_id`, `chunk_index`, `text_sha256`); the code looks them up from
the label.

A draft that fits this shape is not yet a checked draft: `validate_citations` in
`src/drafting/citation_validator.py` decides whether its citations are real.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# A label is this letter and the match's position counted from 1: `E1`, `E2`, ...
EVIDENCE_LABEL_PREFIX = "E"
# At most two digits, so a label is always short enough to show in a problem line.
EVIDENCE_LABEL_PATTERN = r"^E[1-9][0-9]?$"
# Text that holds at least one character that is not whitespace.
NOT_BLANK_PATTERN = r"\S"


def evidence_label(position: int) -> str:
    """Return the label of the retrieved match at `position`, counted from 0: 0 gives `E1`."""
    if position < 0:
        raise ValueError(f"position must be at least 0, got {position}")
    return f"{EVIDENCE_LABEL_PREFIX}{position + 1}"


class ParagraphKind(StrEnum):
    POLICY = "policy"
    OTHER = "other"


class DraftCitation(BaseModel):
    """One piece of evidence a policy paragraph leans on: its label and an exact quote."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(pattern=EVIDENCE_LABEL_PATTERN)
    # Left out of the repr: a quote is policy text and can be long.
    quote: str = Field(pattern=NOT_BLANK_PATTERN, repr=False)


class DraftParagraph(BaseModel):
    """One paragraph of the letter. Only a `policy` paragraph may carry citations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ParagraphKind
    # Left out of the repr: letter text is never printed.
    text: str = Field(pattern=NOT_BLANK_PATTERN, repr=False)
    citations: tuple[DraftCitation, ...] = ()

    @model_validator(mode="after")
    def _only_policy_is_cited(self) -> Self:
        if self.kind is ParagraphKind.OTHER and self.citations:
            raise ValueError("a paragraph of kind 'other' must not have a citation")
        return self


class AppealDraft(BaseModel):
    """A drafted appeal letter, as the model answers it. Not yet checked by the validator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    paragraphs: tuple[DraftParagraph, ...] = Field(min_length=1)
