"""Checks that every policy paragraph of a draft cites evidence that was really retrieved.

This is the gate behind the hard rule "no draft released without a valid citation for every
policy statement". It is a pure function: no model call, no database, no clock. The same
draft and the same matches always give the same answer.

A draft passes only when every `policy` paragraph

1. has at least one citation,
2. cites only labels that are among the retrieved matches, and
3. gives a quote of at least `MIN_QUOTE_CHARACTERS` characters that is found word for word
   in the cited chunk's text.

Whitespace is the only difference a quote may have: runs of spaces and line breaks count as
one space. Everything else is exact. Upper and lower case differ, and a curly quotation mark
is not a straight one, so a quote the model tidied up is blocked. A quote must also start
and end where a word does: "covered at home" is not found in "uncovered at home".

What this does NOT check: that the cited text is the right policy for the claim, or that a
paragraph marked `other` states no policy. A problem carries the paragraph's position and the
label, never quote or chunk text.
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.drafting.schemas import AppealDraft, DraftCitation, ParagraphKind, evidence_label
from src.rag.retrieval import EvidenceMatch

# The shortest quote that counts, measured after whitespace is collapsed. A few words such
# as "is covered" are in almost every chunk and would prove nothing.
MIN_QUOTE_CHARACTERS = 20


class CitationProblemKind(StrEnum):
    POLICY_WITHOUT_CITATION = "policy_without_citation"
    UNKNOWN_LABEL = "unknown_label"
    QUOTE_TOO_SHORT = "quote_too_short"
    QUOTE_NOT_IN_EVIDENCE = "quote_not_in_evidence"


class CitationProblem(BaseModel):
    """One reason a draft is blocked: the kind, the paragraph (counted from 0) and the label.

    `label` is `None` when the paragraph has no citation at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CitationProblemKind
    paragraph_index: int = Field(ge=0)
    label: str | None


class CitationCheck(BaseModel):
    """The validator's answer: every problem found, in paragraph order. None means pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    problems: tuple[CitationProblem, ...]

    @property
    def passed(self) -> bool:
        return not self.problems


def validate_citations(draft: AppealDraft, matches: Sequence[EvidenceMatch]) -> CitationCheck:
    """Check every policy paragraph of `draft` against the retrieved `matches`.

    `matches` is the search result the draft was written from, in its order: the label `E1`
    names the first match, `E2` the second. Returns every problem found, not only the first.
    """
    text_by_label = {
        evidence_label(position): _collapse_whitespace(match.text)
        for position, match in enumerate(matches)
    }
    problems: list[CitationProblem] = []
    for paragraph_index, paragraph in enumerate(draft.paragraphs):
        if paragraph.kind is not ParagraphKind.POLICY:
            continue
        if not paragraph.citations:
            problems.append(
                CitationProblem(
                    kind=CitationProblemKind.POLICY_WITHOUT_CITATION,
                    paragraph_index=paragraph_index,
                    label=None,
                )
            )
        for citation in paragraph.citations:
            problems.extend(
                CitationProblem(kind=kind, paragraph_index=paragraph_index, label=citation.label)
                for kind in _citation_problem_kinds(citation, text_by_label)
            )
    return CitationCheck(problems=tuple(problems))


def _citation_problem_kinds(
    citation: DraftCitation, text_by_label: dict[str, str]
) -> list[CitationProblemKind]:
    kinds: list[CitationProblemKind] = []
    quote = _collapse_whitespace(citation.quote)
    too_short = len(quote) < MIN_QUOTE_CHARACTERS
    if too_short:
        kinds.append(CitationProblemKind.QUOTE_TOO_SHORT)
    evidence_text = text_by_label.get(citation.label)
    if evidence_text is None:
        kinds.append(CitationProblemKind.UNKNOWN_LABEL)
    elif not too_short and not _holds_whole_words(evidence_text, quote):
        kinds.append(CitationProblemKind.QUOTE_NOT_IN_EVIDENCE)
    return kinds


def _holds_whole_words(evidence_text: str, quote: str) -> bool:
    """True when `quote` is in `evidence_text` at a place where it cuts no word in two.

    Every place the quote is found is tried, so a quote that is once part of a longer word
    and once made of whole words passes.
    """
    start = evidence_text.find(quote)
    while start != -1:
        end = start + len(quote)
        if not _splits_a_word(evidence_text, start) and not _splits_a_word(evidence_text, end):
            return True
        start = evidence_text.find(quote, start + 1)
    return False


def _splits_a_word(text: str, position: int) -> bool:
    """True when the characters on both sides of `position` are letters or digits."""
    return 0 < position < len(text) and text[position - 1].isalnum() and text[position].isalnum()


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())
