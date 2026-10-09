from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.db.models import DocumentType, LlmProvider
from src.evaluation.extraction_accuracy import (
    HEADLINE_REASON_FIELD,
    TARGET_FIELD_ACCURACY,
    ConfidenceBand,
    MatchCount,
    build_extraction_accuracy_report,
    confidence_band,
)
from src.extraction.schemas import schema_for
from src.extraction.store import DocumentExtractionRow, DocumentKey, StoredDocument, text_sha256
from src.synth.noise import NoiseLevel
from tests.extraction.helpers import (
    ANSWER_KEYS,
    CLAIM_1,
    CLAIM_2,
    CLAIM_3,
    LETTER,
    MODEL_NAME,
    NOTE,
    PRIOR_AUTH,
    PROMPT_VERSION,
    document_text,
    fields_json,
)

# How many fields a letter, a note and a prior-authorisation record have.
LETTER_FIELDS, NOTE_FIELDS, PRIOR_AUTH_FIELDS = 15, 8, 12


def _document(
    kind: DocumentType = LETTER,
    claim_id: str = CLAIM_1,
    *,
    noise_level: NoiseLevel = NoiseLevel.NONE,
    missing_field: str | None = None,
) -> StoredDocument:
    return StoredDocument(
        source_claim_id=claim_id,
        document_type=kind,
        text=document_text(claim_id, kind),
        answer_key=ANSWER_KEYS[kind],
        noise_level=noise_level,
        missing_field=missing_field,
    )


def _row(
    document: StoredDocument,
    *,
    text: str | None = None,
    confidences: dict[str, float] | None = None,
    **changed: Any,
) -> DocumentExtractionRow:
    """A result for `document`: its answer key with `changed` and `confidences` swapped in."""
    raw = fields_json(document.document_type, **changed)
    for name, confidence in (confidences or {}).items():
        raw[name]["confidence"] = confidence
    fields = schema_for(document.document_type).model_validate(raw)
    return DocumentExtractionRow(
        source_claim_id=document.source_claim_id,
        document_type=document.document_type,
        prompt_version=PROMPT_VERSION,
        model_name=MODEL_NAME,
        provider=LlmProvider.PRIMARY,
        text_sha256=text_sha256(document.text if text is None else text),
        fields=fields.model_dump(mode="json"),
    )


def _by_key(*rows: DocumentExtractionRow) -> dict[DocumentKey, DocumentExtractionRow]:
    return {row.key: row for row in rows}


def _count(matched: int, compared: int) -> MatchCount:
    return MatchCount(matched=matched, compared=compared)


def test_the_target_is_the_stage_3_done_condition() -> None:
    assert Decimal("0.85") == TARGET_FIELD_ACCURACY


def test_perfect_results_are_fully_right_both_ways() -> None:
    documents = [_document(LETTER), _document(NOTE), _document(PRIOR_AUTH)]

    report = build_extraction_accuracy_report(documents, _by_key(*map(_row, documents)))

    values = LETTER_FIELDS + NOTE_FIELDS + PRIOR_AUTH_FIELDS
    assert (report.documents, report.with_result, report.no_result, report.stale) == (3, 3, 0, 0)
    assert report.with_result_only == _count(values, values)
    assert report.all_documents == _count(values, values)
    assert report.all_documents.share == Decimal(1)


def test_a_document_with_no_result_is_all_wrong_in_the_gate_number_only() -> None:
    letter, note = _document(LETTER), _document(NOTE)

    report = build_extraction_accuracy_report([letter, note], _by_key(_row(letter)))

    assert (report.with_result, report.no_result, report.stale) == (1, 1, 0)
    assert report.with_result_only == _count(LETTER_FIELDS, LETTER_FIELDS)
    assert report.all_documents == _count(LETTER_FIELDS, LETTER_FIELDS + NOTE_FIELDS)
    assert report.by_type[NOTE].documents == 1
    assert report.by_type[NOTE].with_result == 0
    assert report.by_type[NOTE].with_result_only == _count(0, 0)
    assert report.by_type[NOTE].all_documents == _count(0, NOTE_FIELDS)
    assert report.by_type[NOTE].fields == {}


def test_a_result_made_from_another_text_is_stale_and_counts_as_no_result() -> None:
    letter = _document(LETTER)

    report = build_extraction_accuracy_report(
        [letter], _by_key(_row(letter, text="an older made-up text"))
    )

    assert (report.with_result, report.no_result, report.stale) == (0, 0, 1)
    assert report.with_result_only == _count(0, 0)
    assert report.all_documents == _count(0, LETTER_FIELDS)
    assert report.by_noise_level[NoiseLevel.NONE] == _count(0, 0)


def test_a_blanked_field_is_right_only_when_the_extracted_value_is_null() -> None:
    read_as_null = _document(LETTER, CLAIM_1, missing_field="member_id")
    read_as_value = _document(LETTER, CLAIM_2, missing_field="member_id")

    report = build_extraction_accuracy_report(
        [read_as_null, read_as_value],
        _by_key(_row(read_as_null, member_id=None), _row(read_as_value)),
    )

    assert report.by_type[LETTER].fields["member_id"] == _count(1, 2)
    assert report.all_documents == _count(2 * LETTER_FIELDS - 1, 2 * LETTER_FIELDS)


def test_counts_are_kept_per_type_and_per_field() -> None:
    letter_1, letter_2 = _document(LETTER, CLAIM_1), _document(LETTER, CLAIM_2)
    note, record = _document(NOTE), _document(PRIOR_AUTH)
    rows = _by_key(
        _row(letter_1, patient_name="Someone Else", letter_date="2008-03-21"),
        _row(letter_2, patient_name="Someone Else"),
        _row(note, procedure_codes=["99214"]),
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
    clean = _document(LETTER, CLAIM_1, noise_level=NoiseLevel.NONE)
    heavy = _document(LETTER, CLAIM_2, noise_level=NoiseLevel.HEAVY)
    heavy_note = _document(NOTE, CLAIM_2, noise_level=NoiseLevel.HEAVY)
    unread = _document(LETTER, CLAIM_3, noise_level=NoiseLevel.LIGHT)
    rows = _by_key(_row(clean), _row(heavy, member_id="MBR-OOOOO1"), _row(heavy_note))

    report = build_extraction_accuracy_report([clean, heavy, heavy_note, unread], rows)

    assert report.by_noise_level == {
        NoiseLevel.NONE: _count(LETTER_FIELDS, LETTER_FIELDS),
        NoiseLevel.LIGHT: _count(0, 0),
        NoiseLevel.HEAVY: _count(LETTER_FIELDS - 1 + NOTE_FIELDS, LETTER_FIELDS + NOTE_FIELDS),
    }
    assert report.by_noise_level[NoiseLevel.LIGHT].share is None


def test_the_headline_reason_counts_every_letter_and_no_other_document() -> None:
    right, wrong, unread = (_document(LETTER, claim) for claim in (CLAIM_1, CLAIM_2, CLAIM_3))
    note = _document(NOTE)
    rows = _by_key(_row(right), _row(wrong, denial_reason_category="duplicate"), _row(note))

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
    note = _document(NOTE)
    row = _row(
        note,
        confidences={"patient_name": 1.0, "member_id": 0.5, "note_date": 0.85},
        member_id="MBR-999999",
    )

    report = build_extraction_accuracy_report([note], _by_key(row))

    assert report.by_confidence == {
        ConfidenceBand.BELOW_080: _count(0, 1),
        ConfidenceBand.FROM_080: _count(1, 1),
        ConfidenceBand.FROM_090: _count(NOTE_FIELDS - 3, NOTE_FIELDS - 3),
        ConfidenceBand.FROM_095: _count(0, 0),
        ConfidenceBand.EXACTLY_1: _count(1, 1),
    }


def test_a_result_for_a_document_that_was_not_given_is_ignored() -> None:
    letter, other = _document(LETTER, CLAIM_1), _document(LETTER, CLAIM_2)

    report = build_extraction_accuracy_report([letter], _by_key(_row(letter), _row(other)))

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
