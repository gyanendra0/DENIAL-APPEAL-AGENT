from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.evaluation.extraction_accuracy import MatchCount
from src.evaluation.rules_agreement import (
    OutcomeMove,
    RulesAgreementReport,
    build_rules_agreement_report,
)
from src.extraction.store import DocumentExtractionRow, StoredDocument
from src.rules.verdict import RuleOutcome
from tests.evaluation.helpers import by_key, result_row, stored_document
from tests.extraction.helpers import CLAIM_1, CLAIM_2, CLAIM_3, LETTER, NOTE, PRIOR_AUTH

PASS = RuleOutcome.PASS
MUST_REVIEW = RuleOutcome.MUST_REVIEW
HARD_FAIL = RuleOutcome.HARD_FAIL

# The made-up letter is dated 2008-03-20, its deadline is 2008-09-16 and its total allowed
# charge is 130.10. So with this "today" and floor its answer key passes every rule.
OPEN_DAY = date(2008, 6, 1)
LATE_DAY = date(2009, 1, 1)
FLOOR = Decimal("25")


def _report(
    documents: list[StoredDocument],
    *rows: DocumentExtractionRow,
    today: date = OPEN_DAY,
    floor: Decimal = FLOOR,
) -> RulesAgreementReport:
    return build_rules_agreement_report(documents, by_key(*rows), today=today, amount_floor=floor)


def _outcomes(passed: int = 0, must_review: int = 0, hard_fail: int = 0) -> dict[RuleOutcome, int]:
    return {PASS: passed, MUST_REVIEW: must_review, HARD_FAIL: hard_fail}


def _move(expected: RuleOutcome, extracted: RuleOutcome, letters: int = 1) -> OutcomeMove:
    return OutcomeMove(expected=expected, extracted=extracted, letters=letters)


def test_a_letter_read_right_gets_the_same_outcome() -> None:
    letter = stored_document(LETTER)

    report = _report([letter], result_row(letter))

    assert (report.letters, report.with_verdict, report.no_verdict) == (1, 1, 0)
    assert report.expected_outcomes == _outcomes(passed=1)
    assert report.same_outcome == MatchCount(matched=1, compared=1)
    assert report.moves == ()


def test_a_wrong_value_the_rules_do_not_read_changes_nothing() -> None:
    letter = stored_document(LETTER)

    report = _report([letter], result_row(letter, member_id="MBR-999999", payer_name="Other"))

    assert report.same_outcome == MatchCount(matched=1, compared=1)


def test_a_deadline_read_as_missing_moves_a_pass_to_must_review() -> None:
    letter = stored_document(LETTER)

    report = _report([letter], result_row(letter, appeal_deadline=None))

    assert report.same_outcome == MatchCount(matched=0, compared=1)
    assert report.moves == (_move(PASS, MUST_REVIEW),)


def test_a_deadline_read_as_missing_moves_a_hard_fail_to_must_review() -> None:
    letter = stored_document(LETTER)

    report = _report([letter], result_row(letter, appeal_deadline=None), today=LATE_DAY)

    assert report.expected_outcomes == _outcomes(hard_fail=1)
    assert report.moves == (_move(HARD_FAIL, MUST_REVIEW),)


def test_an_amount_read_too_small_moves_a_pass_to_hard_fail() -> None:
    letter = stored_document(LETTER)

    report = _report([letter], result_row(letter, total_allowed_charge_amount="13.01"))

    assert report.moves == (_move(PASS, HARD_FAIL),)


def test_a_blanked_deadline_is_missing_in_the_expected_verdict_too() -> None:
    read_as_null = stored_document(LETTER, CLAIM_1, missing_field="appeal_deadline")
    read_as_value = stored_document(LETTER, CLAIM_2, missing_field="appeal_deadline")

    report = _report(
        [read_as_null, read_as_value],
        result_row(read_as_null, appeal_deadline=None),
        result_row(read_as_value),
    )

    assert report.expected_outcomes == _outcomes(must_review=2)
    assert report.same_outcome == MatchCount(matched=1, compared=2)
    assert report.moves == (_move(MUST_REVIEW, PASS),)


def test_a_blanked_letter_date_leaves_the_date_order_check_out_of_the_expected_verdict() -> None:
    # Read as a day after the deadline, the letter date would send the letter to review.
    letter = stored_document(LETTER, missing_field="letter_date")

    report = _report([letter], result_row(letter, letter_date="2008-09-17"))

    assert report.expected_outcomes == _outcomes(passed=1)
    assert report.moves == (_move(PASS, MUST_REVIEW),)


def test_a_blanked_field_the_rules_do_not_read_changes_nothing() -> None:
    letter = stored_document(LETTER, missing_field="member_id")

    report = _report([letter], result_row(letter, member_id=None))

    assert report.expected_outcomes == _outcomes(passed=1)
    assert report.same_outcome == MatchCount(matched=1, compared=1)


def test_a_letter_with_no_result_has_no_verdict_but_an_expected_outcome() -> None:
    read, unread = stored_document(LETTER, CLAIM_1), stored_document(LETTER, CLAIM_2)

    report = _report([read, unread], result_row(read))

    assert (report.letters, report.with_verdict, report.no_verdict) == (2, 1, 1)
    assert report.expected_outcomes == _outcomes(passed=2)
    assert report.same_outcome == MatchCount(matched=1, compared=1)


def test_a_result_made_from_another_text_gives_no_verdict() -> None:
    letter = stored_document(LETTER)

    report = _report([letter], result_row(letter, text="An older text."))

    assert (report.with_verdict, report.no_verdict) == (0, 1)
    assert report.same_outcome == MatchCount(matched=0, compared=0)
    assert report.moves == ()


def test_a_negative_extracted_amount_gives_no_verdict_and_does_not_stop_the_count() -> None:
    negative, fine = stored_document(LETTER, CLAIM_1), stored_document(LETTER, CLAIM_2)

    report = _report(
        [negative, fine],
        result_row(negative, total_allowed_charge_amount="-130.10"),
        result_row(fine),
    )

    assert (report.letters, report.with_verdict, report.no_verdict) == (2, 1, 1)
    assert report.same_outcome == MatchCount(matched=1, compared=1)
    assert report.moves == ()


def test_only_denial_letters_are_read() -> None:
    letter, note, record = (stored_document(kind) for kind in (LETTER, NOTE, PRIOR_AUTH))

    report = _report([letter, note, record], *map(result_row, (letter, note, record)))

    assert (report.letters, report.with_verdict) == (1, 1)


def test_a_result_for_a_letter_that_was_not_asked_for_is_ignored() -> None:
    letter, other = stored_document(LETTER, CLAIM_1), stored_document(LETTER, CLAIM_2)

    report = _report([letter], result_row(letter), result_row(other, appeal_deadline=None))

    assert (report.letters, report.with_verdict) == (1, 1)
    assert report.moves == ()


def test_no_letters_means_nothing_was_compared() -> None:
    report = _report([stored_document(NOTE)])

    assert (report.letters, report.with_verdict, report.no_verdict) == (0, 0, 0)
    assert report.expected_outcomes == _outcomes()
    assert report.same_outcome.share is None
    assert report.moves == ()


def test_today_reaches_the_rules() -> None:
    letter = stored_document(LETTER)

    on_the_deadline = _report([letter], result_row(letter), today=date(2008, 9, 16))
    a_day_late = _report([letter], result_row(letter), today=date(2008, 9, 17))

    assert on_the_deadline.expected_outcomes == _outcomes(passed=1)
    assert a_day_late.expected_outcomes == _outcomes(hard_fail=1)
    assert a_day_late.same_outcome == MatchCount(matched=1, compared=1)


def test_the_amount_floor_reaches_the_rules() -> None:
    letter = stored_document(LETTER)

    at_the_floor = _report([letter], result_row(letter), floor=Decimal("130.10"))
    above_it = _report([letter], result_row(letter), floor=Decimal("130.11"))

    assert at_the_floor.expected_outcomes == _outcomes(passed=1)
    assert above_it.expected_outcomes == _outcomes(hard_fail=1)


def test_a_floor_that_is_not_an_exact_decimal_is_refused() -> None:
    letter = stored_document(LETTER)

    with pytest.raises(ValueError, match="amount floor"):
        build_rules_agreement_report([letter], {}, today=OPEN_DAY, amount_floor=25.0)  # type: ignore[arg-type]


def test_moves_are_counted_per_direction_in_outcome_order() -> None:
    to_fail, to_review, also_to_review = (
        stored_document(LETTER, claim) for claim in (CLAIM_1, CLAIM_2, CLAIM_3)
    )

    report = _report(
        [to_fail, to_review, also_to_review],
        result_row(to_fail, total_allowed_charge_amount="1.00"),
        result_row(to_review, appeal_deadline=None),
        result_row(also_to_review, total_allowed_charge_amount="0.00"),
    )

    assert report.same_outcome == MatchCount(matched=0, compared=3)
    assert report.moves == (_move(PASS, MUST_REVIEW, 2), _move(PASS, HARD_FAIL, 1))


def test_a_report_cannot_be_changed() -> None:
    report = _report([stored_document(LETTER)])

    with pytest.raises(ValidationError):
        report.letters = 5
