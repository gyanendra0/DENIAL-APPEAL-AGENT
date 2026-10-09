import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from src.db.models import DenialReasonCategory, DocumentType
from src.extraction.schemas import (
    EXTRACTION_MAX_OUTPUT_TOKENS,
    ClinicalNoteExtraction,
    DenialLetterExtraction,
    Extracted,
    ExtractedDeniedLine,
    PriorAuthExtraction,
    parse_extraction,
    schema_for,
)
from src.llm.gateway import LlmRequest
from src.synth.clinical_note import ClinicalNoteAnswerKey
from src.synth.denial_letter import DenialLetterAnswerKey, DeniedLineKey
from src.synth.prior_auth import PriorAuthAnswerKey, PriorAuthStatus

# All values are made up. No stored document is used.
DENIED_LINE: dict[str, Any] = {
    "line_number": 2,
    "hcpcs_code": "99213",
    "allowed_charge_amount": "130.10",
    "payment_amount": "0.00",
    "reason_category": "noncovered",
}
LETTER_VALUES: dict[str, Any] = {
    "claim_number": "123456789012345",
    "service_from_date": "2008-03-01",
    "service_thru_date": "2008-03-02",
    "diagnosis_codes": ["4011", "25000"],
    "denied_lines": [DENIED_LINE],
    "total_allowed_charge_amount": "130.10",
    "total_payment_amount": "0.00",
    "denial_reason_category": "noncovered",
    "patient_name": "Made Up Patient",
    "member_id": "MBR-000001",
    "provider_name": "Made Up Practice",
    "payer_name": "Made Up Health Plan",
    "reference_number": "REF-000001",
    "letter_date": "2008-03-20",
    "appeal_deadline": "2008-09-16",
}
NOTE_VALUES: dict[str, Any] = {
    "service_from_date": "2008-03-01",
    "service_thru_date": "2008-03-02",
    "note_date": "2008-03-02",
    "diagnosis_codes": ["4011"],
    "procedure_codes": ["99213"],
    "patient_name": "Made Up Patient",
    "member_id": "MBR-000001",
    "provider_name": "Made Up Practice",
}
PRIOR_AUTH_VALUES: dict[str, Any] = {
    "service_from_date": "2008-03-01",
    "service_thru_date": "2008-03-02",
    "diagnosis_codes": ["4011"],
    "requested_procedure_codes": ["99213", None],
    "patient_name": "Made Up Patient",
    "member_id": "MBR-000001",
    "provider_name": "Made Up Practice",
    "payer_name": "Made Up Health Plan",
    "authorization_number": "AUTH-000001",
    "request_date": "2008-02-10",
    "decision_date": "2008-02-14",
    "status": "approved",
}
VALUES = {
    DocumentType.DENIAL_LETTER: LETTER_VALUES,
    DocumentType.CLINICAL_NOTE: NOTE_VALUES,
    DocumentType.PRIOR_AUTH: PRIOR_AUTH_VALUES,
}


def _fields(document_type: DocumentType, **replaced: Any) -> dict[str, Any]:
    """A whole answer for `document_type`; `replaced` swaps the `{value, confidence}` of a field."""
    fields = {
        name: {"value": value, "confidence": 0.9} for name, value in VALUES[document_type].items()
    }
    return fields | replaced


def _answer(document_type: DocumentType, **replaced: Any) -> str:
    return json.dumps(_fields(document_type, **replaced))


@pytest.mark.parametrize(
    ("document_type", "schema", "answer_key", "count"),
    [
        (DocumentType.DENIAL_LETTER, DenialLetterExtraction, DenialLetterAnswerKey, 15),
        (DocumentType.CLINICAL_NOTE, ClinicalNoteExtraction, ClinicalNoteAnswerKey, 8),
        (DocumentType.PRIOR_AUTH, PriorAuthExtraction, PriorAuthAnswerKey, 12),
    ],
)
def test_each_schema_has_exactly_its_answer_keys_fields(
    document_type: DocumentType, schema: type[BaseModel], answer_key: type[BaseModel], count: int
) -> None:
    assert schema_for(document_type) is schema
    assert list(schema.model_fields) == list(answer_key.model_fields)
    assert len(schema.model_fields) == count


def test_a_denied_line_has_the_answer_keys_fields() -> None:
    assert list(ExtractedDeniedLine.model_fields) == list(DeniedLineKey.model_fields)


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_every_document_type_has_a_schema_whose_fields_are_all_value_and_confidence(
    document_type: DocumentType,
) -> None:
    for field in schema_for(document_type).model_fields.values():
        annotation = field.annotation
        assert isinstance(annotation, type)
        assert issubclass(annotation, Extracted)
        assert list(annotation.model_fields) == ["value", "confidence"]


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_parses_a_whole_answer(document_type: DocumentType) -> None:
    parsed = parse_extraction(document_type, _answer(document_type))

    assert isinstance(parsed, schema_for(document_type))
    assert parsed.patient_name.value == "Made Up Patient"
    assert parsed.patient_name.confidence == 0.9


def test_values_arrive_as_dates_decimals_and_categories() -> None:
    letter = parse_extraction(DocumentType.DENIAL_LETTER, _answer(DocumentType.DENIAL_LETTER))
    record = parse_extraction(DocumentType.PRIOR_AUTH, _answer(DocumentType.PRIOR_AUTH))

    assert isinstance(letter, DenialLetterExtraction)
    assert isinstance(record, PriorAuthExtraction)
    assert letter.letter_date.value == date(2008, 3, 20)
    assert letter.total_allowed_charge_amount.value == Decimal("130.10")
    assert letter.denial_reason_category.value is DenialReasonCategory.NONCOVERED
    assert letter.diagnosis_codes.value == ("4011", "25000")
    assert record.status.value is PriorAuthStatus.APPROVED
    assert record.requested_procedure_codes.value == ("99213", None)


def test_a_list_field_has_one_confidence_for_the_whole_list() -> None:
    letter = parse_extraction(DocumentType.DENIAL_LETTER, _answer(DocumentType.DENIAL_LETTER))

    assert isinstance(letter, DenialLetterExtraction)
    assert letter.denied_lines.confidence == 0.9
    assert letter.denied_lines.value == (
        ExtractedDeniedLine(
            line_number=2,
            hcpcs_code="99213",
            allowed_charge_amount=Decimal("130.10"),
            payment_amount=Decimal("0.00"),
            reason_category=DenialReasonCategory.NONCOVERED,
        ),
    )


def test_a_confidence_inside_a_denied_line_is_rejected() -> None:
    line = DENIED_LINE | {"hcpcs_code": {"value": "99213", "confidence": 1}}
    answer = _answer(DocumentType.DENIAL_LETTER, denied_lines={"value": [line], "confidence": 0.9})

    with pytest.raises(ValidationError, match="denied_lines"):
        parse_extraction(DocumentType.DENIAL_LETTER, answer)


def test_money_written_as_a_json_number_is_read_without_a_float_step() -> None:
    # Hand-written JSON: `json.dumps` would need a float to write a number.
    # As a float, 0.1 is 0.1000000000000000055..., and the long amount loses its last digits.
    answer = (
        _answer(DocumentType.DENIAL_LETTER)
        .replace(
            '"total_allowed_charge_amount": {"value": "130.10"',
            '"total_allowed_charge_amount": {"value": 12345678901234567.89',
        )
        .replace(
            '"total_payment_amount": {"value": "0.00"', '"total_payment_amount": {"value": 0.10'
        )
    )
    assert "12345678901234567.89" in answer
    assert '"value": 0.10' in answer

    letter = parse_extraction(DocumentType.DENIAL_LETTER, answer)

    assert isinstance(letter, DenialLetterExtraction)
    assert letter.total_allowed_charge_amount.value == Decimal("12345678901234567.89")
    assert letter.total_payment_amount.value is not None
    assert letter.total_payment_amount.value.as_tuple().exponent == -2
    assert letter.total_payment_amount.value * 3 == Decimal("0.30")


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_any_value_may_be_null(document_type: DocumentType) -> None:
    nothing_found = {name: {"value": None, "confidence": 0.2} for name in VALUES[document_type]}

    parsed = parse_extraction(document_type, json.dumps(nothing_found))

    assert parsed.member_id.value is None
    assert parsed.member_id.confidence == 0.2


@pytest.mark.parametrize("confidence", [0, 1, 0.5])
def test_accepts_a_confidence_from_zero_to_one(confidence: float) -> None:
    answer = _answer(
        DocumentType.CLINICAL_NOTE, member_id={"value": "MBR-000001", "confidence": confidence}
    )

    assert parse_extraction(DocumentType.CLINICAL_NOTE, answer).member_id.confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01, 97, "high", None])
def test_rejects_a_confidence_outside_zero_to_one(confidence: Any) -> None:
    answer = _answer(
        DocumentType.CLINICAL_NOTE, member_id={"value": "MBR-000001", "confidence": confidence}
    )

    with pytest.raises(ValidationError, match="member_id"):
        parse_extraction(DocumentType.CLINICAL_NOTE, answer)


def test_rejects_a_field_without_a_confidence() -> None:
    answer = _answer(DocumentType.CLINICAL_NOTE, member_id={"value": "MBR-000001"})

    with pytest.raises(ValidationError, match="member_id.confidence"):
        parse_extraction(DocumentType.CLINICAL_NOTE, answer)


def test_rejects_a_bare_value_without_the_wrapper() -> None:
    answer = _answer(DocumentType.CLINICAL_NOTE, member_id="MBR-000001")

    with pytest.raises(ValidationError, match="member_id"):
        parse_extraction(DocumentType.CLINICAL_NOTE, answer)


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_rejects_an_answer_with_a_missing_field(document_type: DocumentType) -> None:
    fields = _fields(document_type)
    del fields["patient_name"]

    with pytest.raises(ValidationError, match="patient_name"):
        parse_extraction(document_type, json.dumps(fields))


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_rejects_an_answer_with_an_unknown_field(document_type: DocumentType) -> None:
    answer = _answer(document_type, appeal_success={"value": True, "confidence": 1})

    with pytest.raises(ValidationError, match="appeal_success"):
        parse_extraction(document_type, answer)


def test_rejects_an_unknown_key_beside_value_and_confidence() -> None:
    answer = _answer(
        DocumentType.CLINICAL_NOTE,
        member_id={"value": "MBR-000001", "confidence": 1, "reason": "printed at the top"},
    )

    with pytest.raises(ValidationError, match="member_id.reason"):
        parse_extraction(DocumentType.CLINICAL_NOTE, answer)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("letter_date", "20 March 2008"),
        ("letter_date", "2008-02-30"),
        ("total_payment_amount", "$0.00"),
        ("total_payment_amount", "nothing"),
        ("denial_reason_category", "not medically necessary"),
        ("patient_name", ""),
        ("patient_name", 7),
        ("diagnosis_codes", "4011"),
        ("diagnosis_codes", ["4011", None]),
        ("denied_lines", [DENIED_LINE | {"line_number": 0}]),
        ("denied_lines", [DENIED_LINE | {"reason_category": "unknown"}]),
        ("denied_lines", [{"line_number": 1}]),
    ],
)
def test_rejects_a_value_of_the_wrong_kind(field: str, value: Any) -> None:
    answer = _answer(DocumentType.DENIAL_LETTER, **{field: {"value": value, "confidence": 0.9}})

    with pytest.raises(ValidationError, match=field):
        parse_extraction(DocumentType.DENIAL_LETTER, answer)


def test_rejects_a_status_that_is_not_one_of_the_two() -> None:
    answer = _answer(DocumentType.PRIOR_AUTH, status={"value": "pending", "confidence": 0.9})

    with pytest.raises(ValidationError, match="status"):
        parse_extraction(DocumentType.PRIOR_AUTH, answer)


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "The claim number is 123456789012345.",
        '{"patient_name": {"value": "Made Up Patient", "confidence": 0.9',
        '```json\n{"patient_name": null}\n```',
    ],
)
def test_an_answer_that_is_not_json_is_an_error(answer: str) -> None:
    with pytest.raises(ValueError, match="(?i)expecting|extra data"):
        parse_extraction(DocumentType.CLINICAL_NOTE, answer)


@pytest.mark.parametrize("answer", ["[]", '"text"', "12", "null"])
def test_an_answer_that_is_json_but_not_an_object_is_an_error(answer: str) -> None:
    with pytest.raises(ValidationError):
        parse_extraction(DocumentType.CLINICAL_NOTE, answer)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_rejects_a_number_that_is_not_a_number(constant: str) -> None:
    answer = _answer(DocumentType.CLINICAL_NOTE).replace(
        '"member_id": {"value": "MBR-000001", "confidence": 0.9',
        f'"member_id": {{"value": "MBR-000001", "confidence": {constant}',
    )
    assert constant in answer

    with pytest.raises(ValueError, match="is not a JSON number"):
        parse_extraction(DocumentType.CLINICAL_NOTE, answer)


def test_a_parsed_extraction_cannot_be_changed() -> None:
    parsed = parse_extraction(DocumentType.CLINICAL_NOTE, _answer(DocumentType.CLINICAL_NOTE))

    with pytest.raises(ValidationError, match="frozen"):
        parsed.member_id.value = "MBR-999999"


def test_the_output_limit_is_one_the_gateway_accepts() -> None:
    request = LlmRequest(
        system="made-up instruction",
        user="made-up document",
        max_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
        prompt_version="v1",
        purpose="extraction",
    )

    assert request.max_tokens == 3000
