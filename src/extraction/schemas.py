"""The typed fields a model must return for each document type.

Every schema has the field names of that type's answer key (`src/synth/`), so scoring is a
field-by-field comparison. A schema checks the KIND of each value, not whether it is right: a
wrong but well-formed value is scored later, a value of the wrong kind is rejected here.
"""

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Generic, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DenialReasonCategory, DocumentType
from src.synth.prior_auth import PriorAuthStatus

# The output limit sent with every extraction call. An estimate, not measured: the largest
# stored answer key is 2,266 bytes of JSON, the confidence wrappers add to it, and a model
# that reasons before answering spends about as many output tokens again on its thinking.
EXTRACTION_MAX_OUTPUT_TOKENS = 3000

NonEmptyText = Annotated[str, Field(min_length=1)]

T = TypeVar("T")


class Extracted(BaseModel, Generic[T]):
    """One extracted field: the value and how sure the model says it is.

    `value` is None when the field is not in the text (the noise step blanks one field on
    some documents). `confidence` is stored as the model gave it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: T | None
    confidence: float = Field(ge=0, le=1)


class ExtractedDeniedLine(BaseModel):
    """One denied service line of a denial letter. It has no confidence of its own: the
    whole list of lines is one field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line_number: int = Field(ge=1)
    hcpcs_code: NonEmptyText | None
    allowed_charge_amount: Decimal
    payment_amount: Decimal
    reason_category: DenialReasonCategory


class DenialLetterExtraction(BaseModel):
    """The 15 fields of a denial letter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_number: Extracted[NonEmptyText]
    service_from_date: Extracted[date]
    service_thru_date: Extracted[date]
    diagnosis_codes: Extracted[tuple[NonEmptyText, ...]]
    denied_lines: Extracted[tuple[ExtractedDeniedLine, ...]]
    total_allowed_charge_amount: Extracted[Decimal]
    total_payment_amount: Extracted[Decimal]
    denial_reason_category: Extracted[DenialReasonCategory]
    patient_name: Extracted[NonEmptyText]
    member_id: Extracted[NonEmptyText]
    provider_name: Extracted[NonEmptyText]
    payer_name: Extracted[NonEmptyText]
    reference_number: Extracted[NonEmptyText]
    letter_date: Extracted[date]
    appeal_deadline: Extracted[date]


class ClinicalNoteExtraction(BaseModel):
    """The 8 fields of a clinical note."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service_from_date: Extracted[date]
    service_thru_date: Extracted[date]
    note_date: Extracted[date]
    diagnosis_codes: Extracted[tuple[NonEmptyText, ...]]
    procedure_codes: Extracted[tuple[NonEmptyText, ...]]
    patient_name: Extracted[NonEmptyText]
    member_id: Extracted[NonEmptyText]
    provider_name: Extracted[NonEmptyText]


class PriorAuthExtraction(BaseModel):
    """The 12 fields of a prior-authorisation record. An entry of
    `requested_procedure_codes` is None when the record prints no code for that line."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service_from_date: Extracted[date]
    service_thru_date: Extracted[date]
    diagnosis_codes: Extracted[tuple[NonEmptyText, ...]]
    requested_procedure_codes: Extracted[tuple[NonEmptyText | None, ...]]
    patient_name: Extracted[NonEmptyText]
    member_id: Extracted[NonEmptyText]
    provider_name: Extracted[NonEmptyText]
    payer_name: Extracted[NonEmptyText]
    authorization_number: Extracted[NonEmptyText]
    request_date: Extracted[date]
    decision_date: Extracted[date]
    status: Extracted[PriorAuthStatus]


ExtractionSchema = DenialLetterExtraction | ClinicalNoteExtraction | PriorAuthExtraction

_SCHEMAS: Mapping[DocumentType, type[ExtractionSchema]] = {
    DocumentType.DENIAL_LETTER: DenialLetterExtraction,
    DocumentType.CLINICAL_NOTE: ClinicalNoteExtraction,
    DocumentType.PRIOR_AUTH: PriorAuthExtraction,
}


def schema_for(document_type: DocumentType) -> type[ExtractionSchema]:
    """Return the schema a document of `document_type` is extracted into."""
    return _SCHEMAS[document_type]


def parse_extraction(document_type: DocumentType, answer: str) -> ExtractionSchema:
    """Turn a model's JSON answer into the schema of `document_type`.

    Raises `ValueError` (pydantic's `ValidationError` is one) when `answer` is not JSON or
    does not fit the schema. The error text may quote the answer, so it must not be logged.
    """
    # Numbers with a decimal point are read straight into `Decimal`: a float in between
    # could change a money amount.
    fields: Any = json.loads(answer, parse_float=Decimal, parse_constant=_reject_constant)
    return schema_for(document_type).model_validate(fields)


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not a JSON number")
