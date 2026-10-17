"""Checks a stored appeal letter again: the model's draft, or a version a person edited.

A letter in `upload_drafts` or `draft_revisions` names each cited chunk by value (`source`,
`source_document_id`, `chunk_index`, `text_sha256`), not by a search label, so
`validate_citations` cannot read it. This check applies the same rules to such a letter, and
the placeholder and fact checks with them. It is the gate behind the hard rule "no draft
released without a valid citation for every policy statement" for a person's edit and for
the approval.

A letter passes only when

1. every `policy` paragraph has at least one citation,
2. every citation names a chunk that is still stored with the same `text_sha256`,
3. every quote has at least `MIN_QUOTE_CHARACTERS` characters and is found in its chunk's
   text under the validator's rules (whitespace collapsed, whole words),
4. no paragraph has text in square brackets, and
5. no paragraph names a wrong dollar amount or "other" as the reason.

The check is pure: no model call, no database, no clock. The chunks are read beforehand by
`read_cited_chunks` in `src/drafting/cited_chunks.py`. A problem carries the paragraph's
position and the citation's position, never letter, quote or chunk text.
"""

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import EvidenceSource
from src.drafting.citation_validator import MIN_QUOTE_CHARACTERS, quote_is_in_evidence
from src.drafting.fact_check import FactProblemKind, find_fact_problems
from src.drafting.placeholder_check import find_placeholder_paragraphs
from src.drafting.schemas import AppealLetter, Citation, ParagraphKind
from src.extraction.schemas import DenialLetterExtraction


class LetterProblemKind(StrEnum):
    POLICY_WITHOUT_CITATION = "policy_without_citation"
    CHUNK_NOT_FOUND = "chunk_not_found"  # no such chunk, or its text changed since the draft
    QUOTE_TOO_SHORT = "quote_too_short"
    QUOTE_NOT_IN_EVIDENCE = "quote_not_in_evidence"
    PLACEHOLDER_TEXT = "placeholder_text"
    AMOUNT_NOT_A_DENIED_LINE = "amount_not_a_denied_line"
    OTHER_AS_REASON = "other_as_reason"
    EDIT_NOT_ALLOWED = "edit_not_allowed"  # see `src/drafting/edit_check.py`


_FACT_KINDS = {
    FactProblemKind.AMOUNT_NOT_A_DENIED_LINE: LetterProblemKind.AMOUNT_NOT_A_DENIED_LINE,
    FactProblemKind.OTHER_AS_REASON: LetterProblemKind.OTHER_AS_REASON,
}


class LetterProblem(BaseModel):
    """One reason a stored letter is refused: the kind and where it is, never any text.

    `paragraph_index` counts from 0. `citation_index` counts the paragraph's citations from 0,
    and is `None` for a problem of the whole paragraph.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: LetterProblemKind
    paragraph_index: int = Field(ge=0)
    citation_index: int | None = Field(default=None, ge=0)


class ChunkRef(BaseModel):
    """The three fields that name a chunk. The fourth, `text_sha256`, tells its version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EvidenceSource
    source_document_id: str
    chunk_index: int = Field(ge=0)

    @classmethod
    def of(cls, citation: Citation) -> "ChunkRef":
        return cls(
            source=citation.source,
            source_document_id=citation.source_document_id,
            chunk_index=citation.chunk_index,
        )


class StoredChunk(BaseModel):
    """A chunk as it is stored now: its text and that text's hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text_sha256: str
    # Left out of the repr: a chunk can be 1,200 characters long.
    text: str = Field(repr=False)


def check_letter(
    letter: AppealLetter,
    chunks: Mapping[ChunkRef, StoredChunk],
    extraction: DenialLetterExtraction,
) -> tuple[LetterProblem, ...]:
    """Return every problem of `letter`, in paragraph order; an empty tuple means it passes.

    `chunks` holds the stored chunks the letter cites (a missing one is not stored any more).
    `extraction` is the denial letter the draft was written from.
    """
    problems: list[LetterProblem] = []
    placeholders = set(find_placeholder_paragraphs(letter))
    facts = find_fact_problems(letter, extraction)
    for paragraph_index, paragraph in enumerate(letter.paragraphs):
        if paragraph.kind is ParagraphKind.POLICY and not paragraph.citations:
            problems.append(
                LetterProblem(
                    kind=LetterProblemKind.POLICY_WITHOUT_CITATION,
                    paragraph_index=paragraph_index,
                )
            )
        for citation_index, citation in enumerate(paragraph.citations):
            kind = _citation_problem_kind(citation, chunks)
            if kind is not None:
                problems.append(
                    LetterProblem(
                        kind=kind, paragraph_index=paragraph_index, citation_index=citation_index
                    )
                )
        if paragraph_index in placeholders:
            problems.append(
                LetterProblem(
                    kind=LetterProblemKind.PLACEHOLDER_TEXT, paragraph_index=paragraph_index
                )
            )
        problems.extend(
            LetterProblem(kind=_FACT_KINDS[fact.kind], paragraph_index=paragraph_index)
            for fact in facts
            if fact.paragraph_index == paragraph_index
        )
    return tuple(problems)


def _citation_problem_kind(
    citation: Citation, chunks: Mapping[ChunkRef, StoredChunk]
) -> LetterProblemKind | None:
    chunk = chunks.get(ChunkRef.of(citation))
    if chunk is None or chunk.text_sha256 != citation.text_sha256:
        return LetterProblemKind.CHUNK_NOT_FOUND
    if len(" ".join(citation.quote.split())) < MIN_QUOTE_CHARACTERS:
        return LetterProblemKind.QUOTE_TOO_SHORT
    if not quote_is_in_evidence(chunk.text, citation.quote):
        return LetterProblemKind.QUOTE_NOT_IN_EVIDENCE
    return None
