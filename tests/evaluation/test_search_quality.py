"""Tests for the search quality check. The search is a stub: no model call, no real question."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from src.db.models import EvidenceSource
from src.evaluation.search_quality import (
    DocumentRef,
    SearchMiss,
    SearchQuestion,
    SearchQuestionFileError,
    SourceShare,
    read_search_questions,
    run_search_quality_check,
    unknown_section_numbers,
)
from tests.rag.helpers import add_document

FIXTURE = Path(__file__).parent / "fixtures" / "search_questions.json"
PROJECT_QUESTIONS = (
    Path(__file__).parents[2] / "config" / "evaluation" / "evidence_search_questions.json"
)


NCD = EvidenceSource.CMS_NCD
MANUAL = EvidenceSource.CMS_MANUAL


def _question(text: str, expected: str, source: EvidenceSource = NCD) -> SearchQuestion:
    return SearchQuestion(question=text, expected_source=source, expected_section_number=expected)


def _ref(section_number: str, source: EvidenceSource = NCD) -> DocumentRef:
    return DocumentRef(source=source, section_number=section_number)


def _refs(*section_numbers: str) -> list[DocumentRef]:
    return [_ref(number) for number in section_numbers]


def _entry(
    question: str = "Covered?", section: object = "10.1", **extra: object
) -> dict[str, object]:
    return {
        "question": question,
        "expected_source": "cms_ncd",
        "expected_section_number": section,
        **extra,
    }


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
    path = _write(tmp_path, [_entry("  Covered? ", " 10.1 ")])

    assert read_search_questions(path) == (_question("Covered?", "10.1"),)


def test_a_question_names_the_source_of_its_document(tmp_path: Path) -> None:
    path = _write(tmp_path, [_entry(section="100-04-29-310", expected_source="cms_manual")])

    (question,) = read_search_questions(path)

    assert question.expected == _ref("100-04-29-310", MANUAL)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ([], "the question file holds no question"),
        ({"question": "Covered?"}, "does not fit the expected shape at the file"),
        (
            [{"question": "Covered?", "expected_source": "cms_ncd"}],
            "at 0.expected_section_number: Field required",
        ),
        (
            [{"question": "Covered?", "expected_section_number": "10.1"}],
            "at 0.expected_source: Field required",
        ),
        ([_entry(expected_source="cms_lcd")], "at 0.expected_source"),
        ([_entry(expected_source=1)], "at 0.expected_source"),
        ([_entry("   ")], "at 0.question"),
        ([_entry(section=10.1)], "at 0.expected_section"),
        ([_entry(note="x")], "at 0.note"),
        ([_entry("Covered?", "10.1"), _entry("covered?", "10.2")], "question 2 repeats"),
    ],
    ids=[
        "empty",
        "not a list",
        "no section",
        "no source",
        "unknown source",
        "source not text",
        "blank",
        "number",
        "extra key",
        "repeated",
    ],
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
    path = _write(tmp_path, [_entry("Secret wording?", 7)])

    with pytest.raises(SearchQuestionFileError) as raised:
        read_search_questions(path)

    assert "Secret wording" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_the_projects_question_file_can_be_used() -> None:
    questions = read_search_questions(PROJECT_QUESTIONS)

    assert len(questions) == 40
    assert len({entry.expected for entry in questions}) == 40
    assert [entry.expected_source for entry in questions] == [NCD] * 20 + [MANUAL] * 20


def test_counts_a_question_as_a_hit_when_its_document_is_among_the_results() -> None:
    questions = [_question("first?", "10.1"), _question("second?", "20.4")]
    answers = {"first?": _refs("30.3", "10.1"), "second?": _refs("20.4")}

    report = run_search_quality_check(questions, answers.__getitem__)

    assert report.questions == 2
    assert report.hits == 2
    assert report.misses == ()
    assert report.hit_share == 1.0
    assert report.by_source == (SourceShare(source=NCD, questions=2, hits=2),)


def test_a_miss_lists_each_document_found_once_in_the_order_found() -> None:
    questions = [_question("first?", "10.1"), _question("second?", "20.4")]
    answers = {"first?": _refs("10.1"), "second?": _refs("30.3", "20.8", "30.3", "20.8", "50.3")}

    report = run_search_quality_check(questions, answers.__getitem__)

    assert report.hits == 1
    assert report.hit_share == 0.5
    assert report.misses == (
        SearchMiss(
            question_number=2, expected=_ref("20.4"), found=tuple(_refs("30.3", "20.8", "50.3"))
        ),
    )


def test_a_search_that_finds_nothing_is_a_miss() -> None:
    report = run_search_quality_check([_question("first?", "10.1")], lambda question: [])

    assert report.hits == 0
    assert report.misses[0].found == ()


def test_a_section_number_must_match_exactly() -> None:
    report = run_search_quality_check(
        [_question("first?", "20.8")], lambda question: _refs("20.8.1")
    )

    assert report.hits == 0


def test_the_same_section_number_in_another_source_is_a_miss() -> None:
    report = run_search_quality_check(
        [_question("first?", "20.8", MANUAL)], lambda question: [_ref("20.8", NCD)]
    )

    assert report.hits == 0
    assert report.misses[0].expected == _ref("20.8", MANUAL)
    assert report.misses[0].found == (_ref("20.8", NCD),)


def test_the_hits_are_also_counted_per_expected_source() -> None:
    questions = [
        _question("first?", "100-02-15-10", MANUAL),
        _question("second?", "10.1"),
        _question("third?", "100-02-15-20", MANUAL),
        _question("fourth?", "100-02-15-30", MANUAL),
    ]
    answers = {
        "first?": [_ref("100-02-15-10", MANUAL)],
        "second?": [_ref("100-02-15-10", MANUAL)],
        "third?": [_ref("10.1"), _ref("100-02-15-20", MANUAL)],
        "fourth?": [_ref("10.1")],
    }

    report = run_search_quality_check(questions, answers.__getitem__)

    assert report.hits == 2
    assert report.by_source == (
        SourceShare(source=NCD, questions=1, hits=0),
        SourceShare(source=MANUAL, questions=3, hits=2),
    )
    assert report.by_source[1].hit_share == 2 / 3
    assert sum(share.questions for share in report.by_source) == report.questions
    assert sum(share.hits for share in report.by_source) == report.hits


def test_asks_each_question_once_in_file_order() -> None:
    asked: list[str] = []

    def search(question: str) -> Sequence[DocumentRef]:
        asked.append(question)
        return []

    run_search_quality_check([_question("first?", "10.1"), _question("second?", "20.4")], search)

    assert asked == ["first?", "second?"]


def test_an_error_of_the_search_leaves_as_it_is() -> None:
    def search(question: str) -> Sequence[DocumentRef]:
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

    unknown = unknown_section_numbers(session, questions)

    assert unknown == _refs("999.12", "999.9003")


def test_a_section_number_stored_only_in_another_source_is_unknown(session: Session) -> None:
    add_document(session, "9001", 1)
    add_document(session, "9002", 1, source=MANUAL)
    questions = [
        _question("first?", "999.9001", MANUAL),
        _question("second?", "999.9002"),
        _question("third?", "999.9002", MANUAL),
    ]

    unknown = unknown_section_numbers(session, questions)

    assert unknown == [_ref("999.9002", NCD), _ref("999.9001", MANUAL)]


def test_no_section_number_is_unknown_when_every_document_is_stored(session: Session) -> None:
    add_document(session, "9001", 1)
    add_document(session, "9001", 1, source=MANUAL)
    questions = [_question("first?", "999.9001"), _question("second?", "999.9001", MANUAL)]

    assert unknown_section_numbers(session, questions) == []
