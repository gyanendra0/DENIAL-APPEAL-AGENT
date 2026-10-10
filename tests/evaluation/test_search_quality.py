"""Tests for the search quality check. The search is a stub: no model call, no real question."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from src.db.models import EvidenceSource
from src.evaluation.search_quality import (
    SearchMiss,
    SearchQuestion,
    SearchQuestionFileError,
    read_search_questions,
    run_search_quality_check,
    unknown_section_numbers,
)
from tests.rag.helpers import add_document

FIXTURE = Path(__file__).parent / "fixtures" / "search_questions.json"
PROJECT_QUESTIONS = (
    Path(__file__).parents[2] / "config" / "evaluation" / "evidence_search_questions.json"
)


def _question(text: str, expected: str) -> SearchQuestion:
    return SearchQuestion(question=text, expected_section_number=expected)


def _write(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def test_reads_the_questions_in_file_order() -> None:
    assert read_search_questions(FIXTURE) == (
        _question("Is the made-up service covered at home?", "999.9001"),
        _question("Is the other made-up service covered?", "999.9002"),
    )


def test_spaces_around_a_question_and_a_section_number_are_dropped(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"question": "  Covered? ", "expected_section_number": " 10.1 "}])

    assert read_search_questions(path) == (_question("Covered?", "10.1"),)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ([], "the question file holds no question"),
        ({"question": "Covered?"}, "does not fit the expected shape at the file"),
        ([{"question": "Covered?"}], "at 0.expected_section_number: Field required"),
        ([{"question": "   ", "expected_section_number": "10.1"}], "at 0.question"),
        ([{"question": "Covered?", "expected_section_number": 10.1}], "at 0.expected_section"),
        (
            [{"question": "Covered?", "expected_section_number": "10.1", "note": "x"}],
            "at 0.note",
        ),
        (
            [
                {"question": "Covered?", "expected_section_number": "10.1"},
                {"question": "covered?", "expected_section_number": "10.2"},
            ],
            "question 2 repeats an earlier question",
        ),
    ],
    ids=["empty", "not a list", "no section", "blank", "number", "extra key", "repeated"],
)
def test_rejects_a_question_file_that_cannot_be_used(
    tmp_path: Path, content: object, message: str
) -> None:
    with pytest.raises(SearchQuestionFileError, match=message):
        read_search_questions(_write(tmp_path, content))


def test_rejects_a_file_that_is_not_json_and_names_the_line(tmp_path: Path) -> None:
    path = tmp_path / "questions.json"
    path.write_text("[\n{broken", encoding="utf-8")

    with pytest.raises(SearchQuestionFileError, match=r"not JSON \(line 2\)"):
        read_search_questions(path)


def test_rejects_a_file_that_cannot_be_read(tmp_path: Path) -> None:
    with pytest.raises(SearchQuestionFileError, match=r"cannot be read \(FileNotFoundError\)"):
        read_search_questions(tmp_path / "missing.json")


def test_a_rejection_does_not_quote_the_question(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"question": "Secret wording?", "expected_section_number": 7}])

    with pytest.raises(SearchQuestionFileError) as raised:
        read_search_questions(path)

    assert "Secret wording" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_the_projects_question_file_can_be_used() -> None:
    questions = read_search_questions(PROJECT_QUESTIONS)

    assert len(questions) == 20
    assert len({entry.expected_section_number for entry in questions}) == 20


def test_counts_a_question_as_a_hit_when_its_document_is_among_the_results() -> None:
    questions = [_question("first?", "10.1"), _question("second?", "20.4")]
    answers = {"first?": ["30.3", "10.1"], "second?": ["20.4"]}

    report = run_search_quality_check(questions, answers.__getitem__)

    assert report.questions == 2
    assert report.hits == 2
    assert report.misses == ()
    assert report.hit_share == 1.0


def test_a_miss_lists_each_document_found_once_in_the_order_found() -> None:
    questions = [_question("first?", "10.1"), _question("second?", "20.4")]
    answers = {"first?": ["10.1"], "second?": ["30.3", "20.8", "30.3", "20.8", "50.3"]}

    report = run_search_quality_check(questions, answers.__getitem__)

    assert report.hits == 1
    assert report.hit_share == 0.5
    assert report.misses == (
        SearchMiss(
            question_number=2,
            expected_section_number="20.4",
            found_section_numbers=("30.3", "20.8", "50.3"),
        ),
    )


def test_a_search_that_finds_nothing_is_a_miss() -> None:
    report = run_search_quality_check([_question("first?", "10.1")], lambda question: [])

    assert report.hits == 0
    assert report.misses[0].found_section_numbers == ()


def test_a_section_number_must_match_exactly() -> None:
    report = run_search_quality_check([_question("first?", "20.8")], lambda question: ["20.8.1"])

    assert report.hits == 0


def test_asks_each_question_once_in_file_order() -> None:
    asked: list[str] = []

    def search(question: str) -> Sequence[str]:
        asked.append(question)
        return []

    run_search_quality_check([_question("first?", "10.1"), _question("second?", "20.4")], search)

    assert asked == ["first?", "second?"]


def test_an_error_of_the_search_leaves_as_it_is() -> None:
    def search(question: str) -> Sequence[str]:
        raise RuntimeError("no vectors")

    with pytest.raises(RuntimeError, match="no vectors"):
        run_search_quality_check([_question("first?", "10.1")], search)


def test_refuses_an_empty_question_list() -> None:
    with pytest.raises(ValueError, match="at least one question"):
        run_search_quality_check([], lambda question: [])


def test_names_the_expected_section_numbers_no_stored_document_has(session: Session) -> None:
    add_document(session, "9001", 1)
    questions = [
        _question("first?", "999.9001"),
        _question("second?", "999.9003"),
        _question("third?", "999.12"),
    ]

    unknown = unknown_section_numbers(session, questions, EvidenceSource.CMS_NCD)

    assert unknown == ["999.12", "999.9003"]


def test_no_section_number_is_unknown_when_every_document_is_stored(session: Session) -> None:
    add_document(session, "9001", 1)

    assert (
        unknown_section_numbers(session, [_question("first?", "999.9001")], EvidenceSource.CMS_NCD)
        == []
    )
