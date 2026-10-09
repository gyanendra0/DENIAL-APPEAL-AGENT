from typing import Any

import pytest

from src.db.models import DocumentType, LlmProvider
from src.extraction.accuracy import FieldMatchCount, count_field_matches, match_fields
from src.extraction.schemas import schema_for
from src.extraction.store import DocumentExtractionRow, StoredDocument, text_sha256
from tests.extraction.helpers import (
    ANSWER_KEYS,
    CLAIM_1,
    CLAIM_2,
    LETTER,
    MODEL_NAME,
    NOTE,
    PATIENT_NAME,
    PRIOR_AUTH,
    PROMPT_VERSION,
    document_text,
    fields_json,
)

FIELD_COUNTS = {LETTER: 15, NOTE: 8, PRIOR_AUTH: 12}


def _document(
    kind: DocumentType = LETTER, claim_id: str = CLAIM_1, missing_field: str | None = None
) -> StoredDocument:
    return StoredDocument(
        source_claim_id=claim_id,
        document_type=kind,
        text=document_text(claim_id, kind),
        answer_key=ANSWER_KEYS[kind],
        missing_field=missing_field,
    )


def _matches(
    kind: DocumentType = LETTER, missing_field: str | None = None, **changed: Any
) -> dict[str, bool]:
    extraction = schema_for(kind).model_validate(fields_json(kind, **changed))
    return match_fields(_document(kind, missing_field=missing_field), extraction)


def _row(document: StoredDocument, **changed: Any) -> DocumentExtractionRow:
    fields = schema_for(document.document_type).model_validate(
        fields_json(document.document_type, **changed)
    )
    return DocumentExtractionRow(
        source_claim_id=document.source_claim_id,
        document_type=document.document_type,
        prompt_version=PROMPT_VERSION,
        model_name=MODEL_NAME,
        provider=LlmProvider.PRIMARY,
        text_sha256=text_sha256(document.text),
        fields=fields.model_dump(mode="json"),
    )


@pytest.mark.parametrize("kind", list(DocumentType))
def test_every_field_of_the_answer_key_matches_a_perfect_extraction(kind: DocumentType) -> None:
    matches = _matches(kind)

    assert list(matches) == list(ANSWER_KEYS[kind])
    assert len(matches) == FIELD_COUNTS[kind]
    assert all(matches.values())


def test_an_amount_written_with_other_digits_is_the_same_amount() -> None:
    assert _matches(total_allowed_charge_amount=130.1)["total_allowed_charge_amount"]
    assert _matches(total_payment_amount="0")["total_payment_amount"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("total_allowed_charge_amount", "130.11"),
        ("patient_name", "Zebulon Quillfeather "),
        ("patient_name", None),
        ("letter_date", "2008-03-21"),
        ("denial_reason_category", "duplicate"),
        ("diagnosis_codes", ["25000", "4011"]),
        ("diagnosis_codes", ["4011"]),
        ("diagnosis_codes", []),
    ],
)
def test_a_value_that_differs_at_all_does_not_match(field: str, value: Any) -> None:
    matches = _matches(**{field: value})

    assert not matches[field]
    assert sum(matches.values()) == 14


def test_the_denied_lines_match_as_one_field_and_only_when_every_line_is_right() -> None:
    line = ANSWER_KEYS[LETTER]["denied_lines"][0]

    assert _matches(denied_lines=[{**line, "allowed_charge_amount": 130.1}])["denied_lines"]
    assert not _matches(denied_lines=[{**line, "hcpcs_code": "99214"}])["denied_lines"]
    assert not _matches(denied_lines=[line, {**line, "line_number": 3}])["denied_lines"]


def test_a_list_entry_that_is_null_in_the_answer_key_must_be_null() -> None:
    assert not _matches(PRIOR_AUTH, requested_procedure_codes=["99213"])[
        "requested_procedure_codes"
    ]


def test_a_blanked_field_is_right_only_when_the_extraction_is_null() -> None:
    assert _matches(missing_field="member_id", member_id=None)["member_id"]
    # The answer key still holds the value, but the text no longer prints it.
    assert not _matches(missing_field="member_id")["member_id"]
    assert all(_matches(missing_field="letter_date", letter_date=None).values())


def test_counts_matches_per_document_type_and_field() -> None:
    letter_1, letter_2, note = _document(), _document(claim_id=CLAIM_2), _document(NOTE)
    rows = {
        letter_1.key: _row(letter_1),
        letter_2.key: _row(letter_2, member_id="MBR-000002", letter_date=None),
        note.key: _row(note),
    }

    report = count_field_matches([letter_1, letter_2, note], rows)

    assert report.documents == {LETTER: 2, NOTE: 1, PRIOR_AUTH: 0}
    assert report.fields[LETTER]["claim_number"] == FieldMatchCount(matched=2, compared=2)
    assert report.fields[LETTER]["member_id"] == FieldMatchCount(matched=1, compared=2)
    assert report.fields[LETTER]["letter_date"] == FieldMatchCount(matched=1, compared=2)
    assert list(report.fields[LETTER]) == list(ANSWER_KEYS[LETTER])
    assert set(report.fields[NOTE].values()) == {FieldMatchCount(matched=1, compared=1)}
    assert report.fields[PRIOR_AUTH] == {}


def test_a_document_without_a_result_or_with_one_for_another_text_is_left_out() -> None:
    extracted, not_extracted, regenerated = _document(), _document(NOTE), _document(PRIOR_AUTH)
    out_of_date = _row(regenerated).model_copy(update={"text_sha256": text_sha256("old text")})

    report = count_field_matches(
        [extracted, not_extracted, regenerated],
        {extracted.key: _row(extracted), regenerated.key: out_of_date},
    )

    assert report.documents == {LETTER: 1, NOTE: 0, PRIOR_AUTH: 0}


def test_the_text_lists_counts_and_field_names_but_no_value() -> None:
    letter, other = _document(), _document(claim_id=CLAIM_2)
    report = count_field_matches(
        [letter, other],
        {letter.key: _row(letter), other.key: _row(other, patient_name="Somebody Else")},
    )

    lines = report.as_text().splitlines()

    assert lines[0] == "exact match with the answer keys (2 documents with a result):"
    assert lines[1] == "  denial_letter (2 documents): 29 of 30 values"
    assert lines[2] == "    claim_number: 2 of 2"
    assert "    patient_name: 1 of 2" in lines
    assert lines[-2:] == [
        "  clinical_note (0 documents): 0 of 0 values",
        "  prior_auth (0 documents): 0 of 0 values",
    ]
    assert PATIENT_NAME not in report.as_text()
    assert "Somebody Else" not in report.as_text()
