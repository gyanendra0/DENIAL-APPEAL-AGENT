from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.db.models import DocumentType
from src.evaluation.extraction_accuracy import (
    HEADLINE_REASON_FIELD,
    TARGET_FIELD_ACCURACY,
    ConfidenceBand,
    MatchCount,
    build_extraction_accuracy_report,
    confidence_band,
)
from src.synth.noise import NoiseLevel
from tests.evaluation.helpers import by_key, result_row, stored_document
from tests.extraction.helpers import (
    ANSWER_KEYS,
    CLAIM_1,
    CLAIM_2,
    CLAIM_3,
    LETTER,
    NOTE,
    PRIOR_AUTH,
)

# How many fields a letter, a note and a prior-authorisation record have.
LETTER_FIELDS, NOTE_FIELDS, PRIOR_AUTH_FIELDS = 15, 8, 12


def _count(matched: int, compared: int) -> MatchCount:
    return MatchCount(matched=matched, compared=compared)


def test_the_target_is_the_stage_3_done_condition() -> None:
    assert Decimal("0.85") == TARGET_FIELD_ACCURACY


def test_perfect_results_are_fully_right_both_ways() -> None:
    documents = [stored_document(LETTER), stored_document(NOTE), stored_document(PRIOR_AUTH)]

    report = build_extraction_accuracy_report(documents, by_key(*map(result_row, documents)))

    values = LETTER_FIELDS + NOTE_FIELDS + PRIOR_AUTH_FIELDS
    assert (report.documents, report.with_result, report.no_result, report.stale) == (3, 3, 0, 0)
    assert report.with_result_only == _count(values, values)
    assert report.all_documents == _count(values, values)
    assert report.all_documents.share == Decimal(1)


def test_a_document_with_no_result_is_all_wrong_in_the_gate_number_only() -> None:
    letter, note = stored_document(LETTER), stored_document(NOTE)

    report = build_extraction_accuracy_report([letter, note], by_key(result_row(letter)))

    assert (report.with_result, report.no_result, report.stale) == (1, 1, 0)
    assert report.with_result_only == _count(LETTER_FIELDS, LETTER_FIELDS)
    assert report.all_documents == _count(LETTER_FIELDS, LETTER_FIELDS + NOTE_FIELDS)
    assert report.by_type[NOTE].documents == 1
    assert report.by_type[NOTE].with_result == 0
    assert report.by_type[NOTE].with_result_only == _count(0, 0)
    assert report.by_type[NOTE].all_documents == _count(0, NOTE_FIELDS)
    assert report.by_type[NOTE].fields == {}


def test_a_result_made_from_another_text_is_stale_and_counts_as_no_result() -> None:
    letter = stored_document(LETTER)

    report = build_extraction_accuracy_report(
        [letter], by_key(result_row(letter, text="an older made-up text"))
    )

    assert (report.with_result, report.no_result, report.stale) == (0, 0, 1)
    assert report.with_result_only == _count(0, 0)
    assert report.all_documents == _count(0, LETTER_FIELDS)
    assert report.by_noise_level[NoiseLevel.NONE] == _count(0, 0)


def test_a_blanked_field_is_right_only_when_the_extracted_value_is_null() -> None:
    read_as_null = stored_document(LETTER, CLAIM_1, missing_field="member_id")
    read_as_value = stored_document(LETTER, CLAIM_2, missing_field="member_id")

    report = build_extraction_accuracy_report(
        [read_as_null, read_as_value],
        by_key(result_row(read_as_null, member_id=None), result_row(read_as_value)),
    )

    assert report.by_type[LETTER].fields["member_id"] == _count(1, 2)
    assert report.all_documents == _count(2 * LETTER_FIELDS - 1, 2 * LETTER_FIELDS)


def test_counts_are_kept_per_type_and_per_field() -> None:
    letter_1, letter_2 = stored_document(LETTER, CLAIM_1), stored_document(LETTER, CLAIM_2)
    note, record = stored_document(NOTE), stored_document(PRIOR_AUTH)
    rows = by_key(
        result_row(letter_1, patient_name="Someone Else", letter_date="2008-03-21"),
        result_row(letter_2, patient_name="Someone Else"),
        result_row(note, procedure_codes=["99214"]),
    )

    report = build_extraction_accuracy_report([letter_1, letter_2, note, record], rows)

    letters = report.by_type[LETTER]
    assert (letters.documents, letters.with_result) == (2, 2)
    assert letters.with_result_only == _count(2 * LETTER_FIELDS - 3, 2 * LETTER_FIELDS)
    assert letters.all_documents == letters.with_result_only
    assert list(letters.fields) == list(ANSWER_KEYS[LETTER])
    assert letters.fields["patient_name"] == _count(0, 2)
    assert letters.fields["letter_date"] == _count(1, 2)
    assert letters.fields["claim_number"] == _count(2, 2)
    assert report.by_type[NOTE].fields["procedure_codes"] == _count(0, 1)
    assert report.by_type[NOTE].with_result_only == _count(NOTE_FIELDS - 1, NOTE_FIELDS)
    assert report.by_type[PRIOR_AUTH].all_documents == _count(0, PRIOR_AUTH_FIELDS)
    right = 2 * LETTER_FIELDS - 3 + NOTE_FIELDS - 1
    assert report.with_result_only == _count(right, 2 * LETTER_FIELDS + NOTE_FIELDS)
    assert report.all_documents == _count(
        right, 2 * LETTER_FIELDS + NOTE_FIELDS + PRIOR_AUTH_FIELDS
    )


def test_counts_are_kept_per_noise_level_for_documents_with_a_result() -> None:
    clean = stored_document(LETTER, CLAIM_1, noise_level=NoiseLevel.NONE)
    heavy = stored_document(LETTER, CLAIM_2, noise_level=NoiseLevel.HEAVY)
    heavy_note = stored_document(NOTE, CLAIM_2, noise_level=NoiseLevel.HEAVY)
    unread = stored_document(LETTER, CLAIM_3, noise_level=NoiseLevel.LIGHT)
    rows = by_key(
        result_row(clean), result_row(heavy, member_id="MBR-OOOOO1"), result_row(heavy_note)
    )

    report = build_extraction_accuracy_report([clean, heavy, heavy_note, unread], rows)

    assert report.by_noise_level == {
        NoiseLevel.NONE: _count(LETTER_FIELDS, LETTER_FIELDS),
        NoiseLevel.LIGHT: _count(0, 0),
        NoiseLevel.HEAVY: _count(LETTER_FIELDS - 1 + NOTE_FIELDS, LETTER_FIELDS + NOTE_FIELDS),
    }
    assert report.by_noise_level[NoiseLevel.LIGHT].share is None


def test_the_headline_reason_counts_every_letter_and_no_other_document() -> None:
    right, wrong, unread = (stored_document(LETTER, claim) for claim in (CLAIM_1, CLAIM_2, CLAIM_3))
    note = stored_document(NOTE)
    rows = by_key(
        result_row(right), result_row(wrong, denial_reason_category="duplicate"), result_row(note)
    )

    report = build_extraction_accuracy_report([right, wrong, unread, note], rows)

    assert report.headline_reason == _count(1, 3)
    assert report.by_type[LETTER].fields[HEADLINE_REASON_FIELD] == _count(1, 2)


@pytest.mark.parametrize(
    ("confidence", "band"),
    [
        (0.0, ConfidenceBand.BELOW_080),
        (0.79, ConfidenceBand.BELOW_080),
        (0.80, ConfidenceBand.FROM_080),
        (0.89, ConfidenceBand.FROM_080),
        (0.90, ConfidenceBand.FROM_090),
        (0.94, ConfidenceBand.FROM_090),
        (0.95, ConfidenceBand.FROM_095),
        (0.99, ConfidenceBand.FROM_095),
        (1.0, ConfidenceBand.EXACTLY_1),
    ],
)
def test_a_score_falls_in_the_band_that_starts_at_or_below_it(
    confidence: float, band: ConfidenceBand
) -> None:
    assert confidence_band(confidence) is band


def test_right_and_wrong_values_are_counted_in_the_band_of_their_own_score() -> None:
    note = stored_document(NOTE)
    row = result_row(
        note,
        confidences={"patient_name": 1.0, "member_id": 0.5, "note_date": 0.85},
        member_id="MBR-999999",
    )

    report = build_extraction_accuracy_report([note], by_key(row))

    assert report.by_confidence == {
        ConfidenceBand.BELOW_080: _count(0, 1),
        ConfidenceBand.FROM_080: _count(1, 1),
        ConfidenceBand.FROM_090: _count(NOTE_FIELDS - 3, NOTE_FIELDS - 3),
        ConfidenceBand.FROM_095: _count(0, 0),
        ConfidenceBand.EXACTLY_1: _count(1, 1),
    }


def test_a_result_for_a_document_that_was_not_given_is_ignored() -> None:
    letter, other = stored_document(LETTER, CLAIM_1), stored_document(LETTER, CLAIM_2)

    report = build_extraction_accuracy_report(
        [letter], by_key(result_row(letter), result_row(other))
    )

    assert (report.documents, report.with_result) == (1, 1)
    assert report.all_documents == _count(LETTER_FIELDS, LETTER_FIELDS)


def test_no_documents_gives_zero_counts_and_no_share() -> None:
    report = build_extraction_accuracy_report([], {})

    assert (report.documents, report.with_result, report.no_result, report.stale) == (0, 0, 0, 0)
    assert report.all_documents == _count(0, 0)
    assert report.all_documents.share is None
    assert report.headline_reason.share is None
    assert list(report.by_type) == list(DocumentType)
    assert list(report.by_noise_level) == list(NoiseLevel)
    assert list(report.by_confidence) == list(ConfidenceBand)


def test_a_share_is_an_exact_decimal() -> None:
    assert _count(17, 20).share == Decimal("0.85")
    assert _count(0, 4).share == Decimal(0)


def test_a_count_refuses_more_matches_than_values() -> None:
    with pytest.raises(ValidationError, match="matched is more than compared"):
        MatchCount(matched=3, compared=2)


def test_the_report_cannot_be_changed() -> None:
    report = build_extraction_accuracy_report([], {})

    with pytest.raises(ValidationError):
        report.documents = 5
