import re
from pathlib import Path

import pytest

import src.drafting.edit_check
from src.drafting.edit_check import find_edit_problems
from src.drafting.letter_check import LetterProblem, LetterProblemKind
from src.drafting.schemas import AppealLetter, LetterParagraph, ParagraphKind
from tests.drafting.letter_helpers import (
    CLOSING_TEXT,
    OTHER_CHUNK_TEXT,
    OTHER_QUOTE,
    citation,
    good_letter,
    letter,
    other,
    policy,
)

# What the check must never do: call a model, read the database, the settings or the clock.
FORBIDDEN_IN_THE_CHECK = re.compile(
    r"src\.llm|src\.config|sqlalchemy|Session|\.today\(|\.now\(|\butcnow\b|\bimport time\b"
)
FIRST = citation()
SECOND = citation(document_id="902", quote=OTHER_QUOTE, chunk_text=OTHER_CHUNK_TEXT)


def _not_allowed(*paragraph_indexes: int) -> tuple[LetterProblem, ...]:
    return tuple(
        LetterProblem(kind=LetterProblemKind.EDIT_NOT_ALLOWED, paragraph_index=index)
        for index in paragraph_indexes
    )


@pytest.mark.parametrize(
    "edited",
    [
        pytest.param(good_letter(), id="unchanged"),
        pytest.param(
            letter(
                other("A made-up new opening."),
                policy(FIRST, SECOND, text="A made-up new policy sentence."),
                other("A made-up new closing."),
            ),
            id="every text changed",
        ),
        pytest.param(letter(policy(FIRST, SECOND), other(CLOSING_TEXT)), id="opening removed"),
        pytest.param(letter(other(), other(CLOSING_TEXT)), id="policy paragraph removed"),
        pytest.param(letter(other(), policy(SECOND), other(CLOSING_TEXT)), id="citation removed"),
        pytest.param(letter(other(), policy(), other(CLOSING_TEXT)), id="every citation removed"),
    ],
)
def test_allows_changed_text_and_removals(edited: AppealLetter) -> None:
    assert find_edit_problems(good_letter(), edited) == ()


def test_refuses_a_changed_paragraph_kind() -> None:
    edited = letter(
        other(),
        policy(FIRST, SECOND),
        LetterParagraph(kind=ParagraphKind.POLICY, text=CLOSING_TEXT, citations=(FIRST,)),
    )

    assert find_edit_problems(good_letter(), edited) == _not_allowed(2)


def test_refuses_an_added_paragraph() -> None:
    edited = letter(other(), policy(FIRST, SECOND), other(CLOSING_TEXT), other("A made-up P.S."))

    assert find_edit_problems(good_letter(), edited) == _not_allowed(3)


def test_refuses_an_added_citation() -> None:
    original = letter(other(), policy(FIRST))

    assert find_edit_problems(original, letter(other(), policy(FIRST, SECOND))) == _not_allowed(1)


def test_refuses_a_changed_quote() -> None:
    changed = citation(quote="Green widgets are never covered.")

    edited = letter(other(), policy(changed, SECOND), other(CLOSING_TEXT))

    assert find_edit_problems(good_letter(), edited) == _not_allowed(1)


def test_refuses_citations_put_in_another_order() -> None:
    edited = letter(other(), policy(SECOND, FIRST), other(CLOSING_TEXT))

    assert find_edit_problems(good_letter(), edited) == _not_allowed(1)


def test_refuses_paragraphs_put_in_another_order() -> None:
    original = letter(other(), policy(FIRST), policy(SECOND))

    edited = letter(other(), policy(SECOND), policy(FIRST))

    assert find_edit_problems(original, edited) == _not_allowed(2)


def test_lines_up_a_paragraph_with_a_later_one_of_the_same_kind() -> None:
    # The edited policy paragraph can only come from the second policy paragraph.
    original = letter(policy(FIRST), other(), policy(SECOND))

    assert find_edit_problems(original, letter(policy(SECOND))) == ()


def test_the_check_reads_no_model_no_database_no_settings_and_no_clock() -> None:
    source = Path(src.drafting.edit_check.__file__).read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]

    assert FORBIDDEN_IN_THE_CHECK.search(code) is None
