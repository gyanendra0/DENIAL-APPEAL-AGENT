import re
from pathlib import Path

import pytest

import src.drafting.letter_check
from src.db.models import EvidenceSource
from src.drafting.letter_check import (
    ChunkRef,
    LetterProblem,
    LetterProblemKind,
    StoredChunk,
    check_letter,
)
from tests.drafting.letter_helpers import (
    CHUNK_TEXT,
    CLOSING_TEXT,
    OTHER_CHUNK_TEXT,
    OTHER_QUOTE,
    citation,
    extraction,
    good_letter,
    letter,
    other,
    policy,
    stored_chunks,
)

# What the check must never do: call a model, read the database, the settings or the clock.
FORBIDDEN_IN_THE_CHECK = re.compile(
    r"src\.llm|src\.config|sqlalchemy|Session|\.today\(|\.now\(|\butcnow\b|\bimport time\b"
)
FIRST_REF = ChunkRef(source=EvidenceSource.CMS_NCD, source_document_id="901", chunk_index=0)


def _problem(
    kind: LetterProblemKind, paragraph_index: int, citation_index: int | None = None
) -> LetterProblem:
    return LetterProblem(kind=kind, paragraph_index=paragraph_index, citation_index=citation_index)


def test_a_letter_whose_citations_are_all_in_their_chunks_passes() -> None:
    assert check_letter(good_letter(), stored_chunks(), extraction()) == ()


def test_a_letter_without_policy_paragraphs_passes_with_no_chunks() -> None:
    assert check_letter(letter(other(), other(CLOSING_TEXT)), {}, extraction()) == ()


def test_refuses_a_policy_paragraph_without_a_citation() -> None:
    problems = check_letter(letter(other(), policy()), stored_chunks(), extraction())

    assert problems == (_problem(LetterProblemKind.POLICY_WITHOUT_CITATION, 1),)


def test_refuses_a_citation_of_a_chunk_that_is_no_longer_stored() -> None:
    chunks = stored_chunks()
    del chunks[FIRST_REF]

    problems = check_letter(good_letter(), chunks, extraction())

    assert problems == (_problem(LetterProblemKind.CHUNK_NOT_FOUND, 1, 0),)


def test_refuses_a_citation_of_a_chunk_whose_text_changed_since_the_draft() -> None:
    chunks = stored_chunks()
    changed = CHUNK_TEXT + " Made-up new sentence."
    chunks[FIRST_REF] = StoredChunk(text_sha256="f" * 64, text=changed)

    problems = check_letter(good_letter(), chunks, extraction())

    assert problems == (_problem(LetterProblemKind.CHUNK_NOT_FOUND, 1, 0),)


def test_refuses_a_quote_shorter_than_the_minimum() -> None:
    edited = letter(other(), policy(citation(quote="are covered")))

    problems = check_letter(edited, stored_chunks(), extraction())

    assert problems == (_problem(LetterProblemKind.QUOTE_TOO_SHORT, 1, 0),)


@pytest.mark.parametrize(
    "quote",
    [
        "Purple widgets are covered when a doctor orders them.",
        # Cuts the first word in two.
        "lue widgets are covered when a doctor",
        # The quote of the other chunk.
        OTHER_QUOTE,
    ],
)
def test_refuses_a_quote_that_is_not_in_its_chunk(quote: str) -> None:
    edited = letter(other(), policy(citation(quote=quote)))

    problems = check_letter(edited, stored_chunks(), extraction())

    assert problems == (_problem(LetterProblemKind.QUOTE_NOT_IN_EVIDENCE, 1, 0),)


def test_names_each_failing_citation_of_a_paragraph_by_its_position() -> None:
    edited = letter(
        policy(
            citation(),
            citation(
                document_id="902",
                quote="A home visit is never covered at all.",
                chunk_text=OTHER_CHUNK_TEXT,
            ),
            citation(document_id="903"),
        )
    )

    problems = check_letter(edited, stored_chunks(), extraction())

    assert problems == (
        _problem(LetterProblemKind.QUOTE_NOT_IN_EVIDENCE, 0, 1),
        _problem(LetterProblemKind.CHUNK_NOT_FOUND, 0, 2),
    )


def test_refuses_placeholder_text() -> None:
    edited = letter(other(), other("Sincerely, [Your Name]"))

    problems = check_letter(edited, {}, extraction())

    assert problems == (_problem(LetterProblemKind.PLACEHOLDER_TEXT, 1),)


def test_refuses_a_dollar_amount_that_is_no_denied_line() -> None:
    edited = letter(other("I appeal the made-up amount of $999.99."))

    problems = check_letter(edited, {}, extraction())

    assert problems == (_problem(LetterProblemKind.AMOUNT_NOT_A_DENIED_LINE, 0),)


def test_refuses_other_given_as_the_reason() -> None:
    edited = letter(other('The claim was denied for the reason "other".'))

    problems = check_letter(edited, {}, extraction())

    assert problems == (_problem(LetterProblemKind.OTHER_AS_REASON, 0),)


def test_gives_every_problem_in_paragraph_order() -> None:
    edited = letter(
        other("Sincerely, [Your Name], about $999.99."),
        policy(),
        policy(citation(quote="are covered")),
    )

    problems = check_letter(edited, stored_chunks(), extraction())

    assert problems == (
        _problem(LetterProblemKind.PLACEHOLDER_TEXT, 0),
        _problem(LetterProblemKind.AMOUNT_NOT_A_DENIED_LINE, 0),
        _problem(LetterProblemKind.POLICY_WITHOUT_CITATION, 1),
        _problem(LetterProblemKind.QUOTE_TOO_SHORT, 2, 0),
    )


def test_a_problem_shows_no_letter_quote_or_chunk_text() -> None:
    problem = _problem(LetterProblemKind.QUOTE_NOT_IN_EVIDENCE, 1, 0)

    assert set(problem.model_dump()) == {"kind", "paragraph_index", "citation_index"}


def test_a_stored_chunk_hides_its_text_from_the_repr() -> None:
    assert CHUNK_TEXT not in repr(StoredChunk(text_sha256="0" * 64, text=CHUNK_TEXT))


def test_the_check_reads_no_model_no_database_no_settings_and_no_clock() -> None:
    source = Path(src.drafting.letter_check.__file__).read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]

    assert FORBIDDEN_IN_THE_CHECK.search(code) is None
