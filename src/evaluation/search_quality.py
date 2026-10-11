"""Measures how often the evidence search brings back the document a question is about.

The input is a small hand-made question file: each question names the one document expected
back, by its source and its `section_number`. A question is a hit when a chunk of that
document is among the chunks the search returned. The share of hits is recorded, it is not a
gate: about 20 questions per source show whether the search works at all, they do not prove
it is good.

Nothing here calls a model. The caller passes the search in as a function, so the paid call
stays in the command and a test can pass a stub.
"""

import json
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import EvidenceDocument, EvidenceSource


class SearchQuestionFileError(Exception):
    """The question file cannot be used. The message says why and quotes no question."""


class DocumentRef(BaseModel):
    """One evidence document, named by its source and its `section_number`.

    A section number alone is not enough: it is unique only within a source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EvidenceSource
    section_number: str


class SearchQuestion(BaseModel):
    """One hand-made question and the document a good search brings back for it."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, str_strip_whitespace=True)

    question: str = Field(min_length=1)
    # Not strict: the file holds the source as text, such as "cms_manual".
    expected_source: EvidenceSource = Field(strict=False)
    expected_section_number: str = Field(min_length=1)

    @property
    def expected(self) -> DocumentRef:
        """The document this question expects back."""
        return DocumentRef(source=self.expected_source, section_number=self.expected_section_number)


class SearchMiss(BaseModel):
    """A question whose expected document was not among the results.

    `question_number` counts from 1 in file order. `found` are the documents that came back
    instead, the closest first, each once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_number: int = Field(ge=1)
    expected: DocumentRef
    found: tuple[DocumentRef, ...]


class SourceShare(BaseModel):
    """How many of the questions that expect a document of one source were a hit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EvidenceSource
    questions: int = Field(ge=1)
    hits: int = Field(ge=0)

    @property
    def hit_share(self) -> float:
        """The share of this source's questions that were a hit, 0 to 1."""
        return self.hits / self.questions


class SearchQualityReport(BaseModel):
    """How many questions had their expected document among the results.

    `by_source` splits the same count by the source the question expects, in the order of
    `EvidenceSource`; a source no question expects is left out.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    questions: int = Field(ge=1)
    hits: int = Field(ge=0)
    by_source: tuple[SourceShare, ...]
    misses: tuple[SearchMiss, ...]

    @property
    def hit_share(self) -> float:
        """The share of questions that were a hit, 0 to 1."""
        return self.hits / self.questions


_QUESTIONS = TypeAdapter(tuple[SearchQuestion, ...])


def read_search_questions(path: Path) -> tuple[SearchQuestion, ...]:
    """Read the question file at `path`: a JSON list of question + expected source and number.

    Raises `SearchQuestionFileError` when the file is not such a list, holds no question, or
    holds the same question twice (compared without regard to letter case).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise SearchQuestionFileError(
            f"the question file cannot be read ({type(error).__name__})"
        ) from None
    except json.JSONDecodeError as error:
        raise SearchQuestionFileError(
            f"the question file is not JSON (line {error.lineno})"
        ) from None
    try:
        questions = _QUESTIONS.validate_python(raw)
    except ValidationError as error:
        first = error.errors(include_input=False)[0]
        place = ".".join(str(part) for part in first["loc"]) or "the file"
        raise SearchQuestionFileError(
            f"the question file does not fit the expected shape at {place}: {first['msg']}"
        ) from None
    if not questions:
        raise SearchQuestionFileError("the question file holds no question")
    seen: set[str] = set()
    for number, entry in enumerate(questions, start=1):
        key = entry.question.casefold()
        if key in seen:
            raise SearchQuestionFileError(f"question {number} repeats an earlier question")
        seen.add(key)
    return questions


def unknown_section_numbers(
    session: Session, questions: Sequence[SearchQuestion]
) -> list[DocumentRef]:
    """Return the expected documents that are not stored, by source, then section number.

    Each expected section number is looked up in the source its question names. A question
    that expects a document the corpus does not hold can never be a hit, so a typing mistake
    in the file would be counted as a failure of the search.
    """
    unknown: list[DocumentRef] = []
    for source in EvidenceSource:
        expected = {
            entry.expected_section_number for entry in questions if entry.expected_source == source
        }
        if not expected:
            continue
        stored = set(
            session.scalars(
                select(EvidenceDocument.section_number).where(
                    EvidenceDocument.source == source,
                    EvidenceDocument.section_number.in_(expected),
                )
            )
        )
        unknown.extend(
            DocumentRef(source=source, section_number=number)
            for number in sorted(expected - stored)
        )
    return unknown


def run_search_quality_check(
    questions: Sequence[SearchQuestion], search: Callable[[str], Sequence[DocumentRef]]
) -> SearchQualityReport:
    """Ask every question through `search` and count the ones that found their document.

    `search` takes the question text and returns the document of each chunk found, the
    closest first; how many it returns (the "top k") and which sources it looks at are the
    caller's choice. It is called once per question, in file order, and whatever it raises
    leaves as it is. `questions` must not be empty.
    """
    if not questions:
        raise ValueError("at least one question is needed")
    misses = []
    for number, entry in enumerate(questions, start=1):
        found = tuple(dict.fromkeys(search(entry.question)))
        if entry.expected not in found:
            misses.append(SearchMiss(question_number=number, expected=entry.expected, found=found))
    by_source = []
    for source in EvidenceSource:
        asked = sum(1 for entry in questions if entry.expected_source == source)
        if asked:
            missed = sum(1 for miss in misses if miss.expected.source == source)
            by_source.append(SourceShare(source=source, questions=asked, hits=asked - missed))
    return SearchQualityReport(
        questions=len(questions),
        hits=len(questions) - len(misses),
        by_source=tuple(by_source),
        misses=tuple(misses),
    )
