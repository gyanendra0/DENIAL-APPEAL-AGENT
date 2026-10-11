"""Tests for the score file and the acceptance share. Made-up scores; no database, no model."""

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.evaluation.review_rubric import RejectReason, Verdict
from src.evaluation.review_score import (
    SCORE_COLUMNS,
    ReviewScore,
    ScoreFileError,
    ShareCount,
    build_review_report,
    check_review_scores,
    count_without_verdict,
    read_review_scores,
)

FIXTURE = Path(__file__).parent / "fixtures" / "review_scores.csv"
HEADER = ",".join(SCORE_COLUMNS)
CLAIMS = [f"81000000000000{number}" for number in range(1, 6)]
FINGERPRINTS = dict(zip(CLAIMS, (letter * 64 for letter in "abcde"), strict=True))
HIDDEN_NOTE = "a made-up note nobody may print"


def _row(number: int, verdict: str = "", reason: str = "", note: str = "") -> str:
    claim_id = CLAIMS[number - 1]
    return f"{claim_id},{FINGERPRINTS[claim_id]},{verdict},{reason},{note}"


def _write(tmp_path: Path, *rows: str, header: str = HEADER) -> Path:
    path = tmp_path / "review_scores.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def _problems(path: Path) -> tuple[str, ...]:
    with pytest.raises(ScoreFileError) as raised:
        read_review_scores(path)
    assert raised.value.__cause__ is None
    return raised.value.problems


def _score(number: int, verdict: Verdict | None, reason: RejectReason | None = None) -> ReviewScore:
    claim_id = CLAIMS[number - 1]
    return ReviewScore(
        line=number + 1,
        claim_id=claim_id,
        draft_sha256=FINGERPRINTS[claim_id],
        verdict=verdict,
        reject_reason=reason,
    )


def _scores(accepted: int, rejected: int) -> list[ReviewScore]:
    """`accepted` + `rejected` scores on made-up claims of their own."""
    scores = []
    for number in range(accepted + rejected):
        is_accepted = number < accepted
        scores.append(
            ReviewScore(
                line=number + 2,
                claim_id=f"{820000000000000 + number}",
                draft_sha256="f" * 64,
                verdict=Verdict.ACCEPT if is_accepted else Verdict.REJECT,
                reject_reason=None if is_accepted else RejectReason.POOR_WORDING,
            )
        )
    return scores


def test_reads_the_scores_in_file_order() -> None:
    scores = read_review_scores(FIXTURE)

    assert [score.claim_id for score in scores] == CLAIMS
    assert [score.line for score in scores] == [2, 3, 4, 5, 6]
    assert [score.draft_sha256 for score in scores] == list(FINGERPRINTS.values())
    assert [score.verdict for score in scores] == [
        Verdict.ACCEPT,
        Verdict.ACCEPT_LIGHT_EDITS,
        Verdict.REJECT,
        Verdict.ACCEPT,
        Verdict.REJECT,
    ]
    assert [score.reject_reason for score in scores] == [
        None,
        None,
        RejectReason.WRONG_FACT,
        None,
        RejectReason.OTHER,
    ]
    assert scores[2].note == "the made-up date differs, see paragraph 2"


def test_a_row_that_is_not_filled_in_is_read_with_no_verdict(tmp_path: Path) -> None:
    scores = read_review_scores(_write(tmp_path, _row(1), _row(2, "accept")))

    assert [score.verdict for score in scores] == [None, Verdict.ACCEPT]
    assert count_without_verdict(scores) == 1


def test_a_file_saved_by_a_spreadsheet_program_is_read(tmp_path: Path) -> None:
    path = tmp_path / "review_scores.csv"
    content = "\r\n".join([HEADER, _row(1, " accept "), _row(2, "reject", "other"), "", ",,,,"])
    path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    scores = read_review_scores(path)

    assert [score.verdict for score in scores] == [Verdict.ACCEPT, Verdict.REJECT]


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        (
            _row(1, "maybe"),
            "line 2: verdict: Input should be 'accept', 'accept_light_edits' or 'reject'",
        ),
        (
            _row(1, "reject", "too_long"),
            "line 2: reject_reason: Input should be 'wrong_fact', 'no_policy_argument',"
            " 'citation_does_not_fit', 'poor_wording' or 'other'",
        ),
        (_row(1, "reject"), "line 2: a reject needs a reject_reason"),
        (_row(1, "accept", "other"), "line 2: only a reject has a reject_reason"),
        (
            _row(1, "accept_light_edits", "poor_wording"),
            "line 2: only a reject has a reject_reason",
        ),
        (_row(1, "", "other"), "line 2: only a reject has a reject_reason"),
        (_row(1, "accept") + ",extra", "line 2: 5 values expected, 6 found"),
        (f"{CLAIMS[0]},accept", "line 2: 5 values expected, 2 found"),
    ],
)
def test_a_row_outside_the_rubric_is_refused_with_a_plain_message(
    tmp_path: Path, row: str, problem: str
) -> None:
    assert _problems(_write(tmp_path, row)) == (problem,)


def test_a_changed_claim_id_or_fingerprint_cell_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, f"12345,{'a' * 64},accept,,", f"{CLAIMS[1]},not-a-hash,accept,,")

    first, second = _problems(path)

    assert first.startswith("line 2: claim_id: String should match pattern")
    assert second.startswith("line 3: draft_sha256: String should match pattern")


def test_a_claim_scored_twice_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, _row(1, "accept"), _row(2, "accept"), _row(1, "reject", "other"))

    assert _problems(path) == ("line 4: the claim is already scored on line 2",)


def test_every_problem_is_reported_and_none_quotes_a_value_of_the_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _row(1, "maybe", note=HIDDEN_NOTE),
        _row(2, "accept"),
        _row(3, "reject", note=HIDDEN_NOTE),
    )

    problems = _problems(path)

    assert [problem.split(":")[0] for problem in problems] == ["line 2", "line 4"]
    for problem in problems:
        assert HIDDEN_NOTE not in problem
        assert "maybe" not in problem
        assert CLAIMS[0] not in problem


def test_a_note_longer_than_the_limit_is_refused(tmp_path: Path) -> None:
    (problem,) = _problems(_write(tmp_path, _row(1, "accept", note="x" * 1001)))

    assert problem == "line 2: note: String should have at most 1000 characters"


@pytest.mark.parametrize(
    "header",
    ["claim_id,verdict,reject_reason,note", "Claim,Hash,Verdict,Reason,Note", ""],
)
def test_a_first_line_that_is_not_the_column_names_is_refused(tmp_path: Path, header: str) -> None:
    path = _write(tmp_path, _row(1, "accept"), header=header)

    assert _problems(path) == (
        "the first line must be: claim_id,draft_sha256,verdict,reject_reason,note",
    )


def test_an_empty_file_and_a_file_with_no_row_are_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")

    assert _problems(empty)[0].startswith("the first line must be: ")
    assert _problems(_write(tmp_path)) == ("the score file holds no row",)


def test_a_file_that_cannot_be_read_is_refused_with_a_plain_message(tmp_path: Path) -> None:
    not_text = tmp_path / "review_scores.csv"
    not_text.write_bytes(b"\xff\xfe\x00\x00")

    assert _problems(tmp_path / "missing.csv") == (
        "the score file cannot be read (FileNotFoundError)",
    )
    assert _problems(not_text) == ("the score file cannot be read (UnicodeDecodeError)",)


def test_a_file_the_csv_reader_refuses_is_refused_with_a_plain_message(tmp_path: Path) -> None:
    # One value far above the reader's size limit for a single value.
    path = _write(tmp_path, _row(1, "accept", note="x" * (csv.field_size_limit() + 1)))

    assert _problems(path) == ("the score file is not a CSV file",)


def test_a_score_does_not_show_its_note() -> None:
    score = _score(1, Verdict.ACCEPT).model_copy(update={"note": HIDDEN_NOTE})

    assert HIDDEN_NOTE not in repr(score)


def test_scores_that_fit_the_stored_drafts_have_no_problem() -> None:
    assert check_review_scores(read_review_scores(FIXTURE), FINGERPRINTS, size=5) == []


def test_a_removed_or_added_row_is_found_by_the_sample_size() -> None:
    scores = read_review_scores(FIXTURE)

    assert check_review_scores(scores[:4], FINGERPRINTS, size=5) == [
        "4 rows found, 5 expected: every sampled draft is scored exactly once"
    ]
    assert check_review_scores(scores, FINGERPRINTS, size=4) == [
        "5 rows found, 4 expected: every sampled draft is scored exactly once"
    ]


def test_a_row_for_a_draft_that_was_drafted_again_is_refused() -> None:
    stored = {**FINGERPRINTS, CLAIMS[2]: "9" * 64}

    assert check_review_scores(read_review_scores(FIXTURE), stored, size=5) == [
        "line 4: the stored draft is not the one this row was written for (it was drafted"
        " again): export the sample again"
    ]


def test_a_row_for_a_claim_with_no_reviewable_draft_is_refused() -> None:
    stored = {claim_id: FINGERPRINTS[claim_id] for claim_id in CLAIMS[:4]}

    assert check_review_scores(read_review_scores(FIXTURE), stored, size=5) == [
        "line 6: no draft that can be reviewed is stored for this claim"
    ]


def test_the_report_counts_the_verdicts_the_reasons_and_the_two_groups() -> None:
    has_citation = dict(zip(CLAIMS, [True, False, True, False, False], strict=True))

    report = build_review_report(read_review_scores(FIXTURE), has_citation)

    assert report.verdicts == {
        Verdict.ACCEPT: 2,
        Verdict.ACCEPT_LIGHT_EDITS: 1,
        Verdict.REJECT: 2,
    }
    assert report.reject_reasons == {
        RejectReason.WRONG_FACT: 1,
        RejectReason.NO_POLICY_ARGUMENT: 0,
        RejectReason.CITATION_DOES_NOT_FIT: 0,
        RejectReason.POOR_WORDING: 0,
        RejectReason.OTHER: 1,
    }
    assert report.with_citation == ShareCount(accepted=1, scored=2)
    assert report.without_citation == ShareCount(accepted=2, scored=3)
    assert report.overall == ShareCount(accepted=3, scored=5)
    assert report.as_text().splitlines() == [
        "scored 5 drafts (acceptance target 60.00%)",
        "  accept: 2",
        "  accept_light_edits: 1",
        "  reject: 2",
        "    wrong_fact: 1",
        "    no_policy_argument: 0",
        "    citation_does_not_fit: 0",
        "    poor_wording: 0",
        "    other: 1",
        "  acceptance share: 3 of 5 (60.00%), meets the target",
        "  drafts with a citation: 1 of 2 (50.00%)",
        "  drafts without a citation: 2 of 3 (66.67%)",
    ]


@pytest.mark.parametrize(
    ("accepted", "rejected", "met", "shown"),
    [
        (18, 12, True, "18 of 30 (60.00%), meets the target"),
        (17, 13, False, "17 of 30 (56.67%), below the target"),
        (30, 0, True, "30 of 30 (100.00%), meets the target"),
        (0, 30, False, "0 of 30 (0.00%), below the target"),
        # 59.996% would round to 60.00%: it is printed as what it is, below the target.
        (14999, 10001, False, "14999 of 25000 (59.99%), below the target"),
    ],
)
def test_exactly_60_percent_meets_the_target_and_anything_below_does_not(
    accepted: int, rejected: int, met: bool, shown: str
) -> None:
    scores = _scores(accepted, rejected)

    report = build_review_report(scores, dict.fromkeys((s.claim_id for s in scores), False))

    assert report.target_met is met
    assert f"  acceptance share: {shown}" in report.as_text().splitlines()


def test_a_group_with_no_draft_has_no_share() -> None:
    scores = _scores(1, 1)

    report = build_review_report(scores, dict.fromkeys((s.claim_id for s in scores), True))

    assert report.without_citation.share is None
    assert "  drafts without a citation: 0 of 0 (n/a)" in report.as_text().splitlines()


def test_no_share_is_worked_out_from_a_review_that_is_not_finished() -> None:
    scores = [_score(1, Verdict.ACCEPT), _score(2, None)]

    with pytest.raises(ValueError, match="a score has no verdict yet"):
        build_review_report(scores, dict.fromkeys(CLAIMS, False))
    with pytest.raises(ValueError, match="at least one score is needed"):
        build_review_report([], {})


def test_more_accepted_than_scored_is_not_a_count() -> None:
    with pytest.raises(ValidationError, match="more drafts accepted than scored"):
        ShareCount(accepted=2, scored=1)
