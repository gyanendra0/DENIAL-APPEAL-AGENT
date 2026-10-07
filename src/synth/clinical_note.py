"""Clinical note generator v1: turn one denied claim into a fabricated visit note.

Every note is made up. The dates and the diagnosis and procedure codes come from the synthetic
claims sample; the patient name, member id and provider come from `src.synth.identity`, so the
note describes the same people as the claim's denial letter. No real patient or provider
appears in a note.

The note is the provider's own short record of the visit, written as if before the denial:

- It prints codes only. No table of code meanings is loaded, so a description of a code would
  be an invented clinical fact. The wording around the codes is generic.
- It never shows money, the denial reason, the payer, the claim number or a clinician's name.
  The practice signs the note.
- It never shows the appeal-success proxy, its chance, the amount band or the split.

How one note is made:

- The only choice of its own is the template, a repeatable draw of the text
  `doc:<generator version>:<seed>:clinical_note:<claim id>:template`. The note date is the
  claim's last service date, with no draw.
- The answer key (every value the note prints) is built first. A template then writes the text
  from the answer key alone, so the text cannot hold a value the key lacks.

Any change to a template, a rule or the draw text needs a new `GENERATOR_VERSION`, because the
same seed would no longer give the same text.
"""

from collections.abc import Callable, Sequence
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DocumentType
from src.ml.labels import ClaimId, ClaimLabel
from src.synth.formats import (
    CODE_NOT_PROVIDED,
    DocumentClaim,
    iso_date,
    long_date,
    service_dates,
    us_date,
)
from src.synth.identity import MAX_SEED, check_seed, claim_identity, document_draw, pick

GENERATOR_VERSION = "v1"
DOCUMENT_TYPE = DocumentType.CLINICAL_NOTE

TEMPLATE_IDS = ("visit_note", "encounter_summary", "chart_entry")


class NoteLine(Protocol):
    """The two fields of a service line that the note reads."""

    @property
    def line_number(self) -> int: ...

    @property
    def hcpcs_code(self) -> str | None: ...


class ClinicalNoteAnswerKey(BaseModel):
    """Every value printed in one clinical note, real or fabricated.

    `procedure_codes` holds the codes of all of the claim's lines, denied or not, in line
    order, each code once. When it or `diagnosis_codes` is empty, the note says "not provided".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    service_from_date: date
    service_thru_date: date
    note_date: date
    diagnosis_codes: tuple[str, ...]
    procedure_codes: tuple[str, ...]
    patient_name: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)


class ClinicalNote(BaseModel):
    """One generated clinical note: the fields of a `generated_documents` row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    document_type: DocumentType
    template_id: str = Field(min_length=1, max_length=40)
    seed: int = Field(ge=0, le=MAX_SEED)
    generator_version: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1)
    answer_key: ClinicalNoteAnswerKey


def generate_clinical_note(
    claim: DocumentClaim, lines: Sequence[NoteLine], label: ClaimLabel, seed: int
) -> ClinicalNote:
    """Write the clinical note of one denied claim.

    `lines` are all of the claim's lines, in any order, and `label` is that claim's label. The
    label only decides whether the claim gets a note; nothing of it is printed. The same claim,
    seed and `GENERATOR_VERSION` always give the same note.
    """
    claim_id = claim.source_claim_id
    check_seed(seed)
    if label.source_claim_id != claim_id:
        raise ValueError(f"the label is for claim {label.source_claim_id}, not claim {claim_id}")
    if not label.is_denied:
        raise ValueError(f"claim {claim_id} is not denied, so it gets no clinical note")

    in_line_order = sorted(lines, key=lambda line: line.line_number)
    identity = claim_identity(claim_id, seed)
    key = ClinicalNoteAnswerKey(
        service_from_date=claim.claim_from_date,
        service_thru_date=claim.claim_thru_date,
        note_date=claim.claim_thru_date,
        diagnosis_codes=tuple(claim.diagnosis_codes),
        # A dict keeps the first place of each code and drops the repeats.
        procedure_codes=tuple(
            dict.fromkeys(line.hcpcs_code for line in in_line_order if line.hcpcs_code)
        ),
        patient_name=identity.patient_name,
        member_id=identity.member_id,
        provider_name=identity.provider_name,
    )
    template_id = pick(
        TEMPLATE_IDS, document_draw(GENERATOR_VERSION, seed, DOCUMENT_TYPE, claim_id, "template")
    )
    return ClinicalNote(
        source_claim_id=claim_id,
        document_type=DOCUMENT_TYPE,
        template_id=template_id,
        seed=seed,
        generator_version=GENERATOR_VERSION,
        text=_RENDERERS[template_id](key),
        answer_key=key,
    )


def _service_dates(key: ClinicalNoteAnswerKey, show: Callable[[date], str]) -> str:
    return service_dates(key.service_from_date, key.service_thru_date, show)


def _codes(codes: Sequence[str], separator: str) -> str:
    return separator.join(codes) or CODE_NOT_PROVIDED


def _visit_note(key: ClinicalNoteAnswerKey) -> str:
    return "\n".join(
        [
            key.provider_name,
            "Visit note",
            "",
            f"Date of note: {long_date(key.note_date)}",
            f"Patient: {key.patient_name}",
            f"Member ID: {key.member_id}",
            "",
            f"The patient was seen on {_service_dates(key, long_date)} for the conditions "
            "coded below.",
            "",
            f"Diagnosis codes: {_codes(key.diagnosis_codes, ', ')}",
            f"Procedure codes for the services provided: {_codes(key.procedure_codes, ', ')}",
            "",
            f"Signed: {key.provider_name}",
        ]
    )


def _encounter_summary(key: ClinicalNoteAnswerKey) -> str:
    return "\n".join(
        [
            "ENCOUNTER SUMMARY",
            "",
            f"Practice:           {key.provider_name}",
            f"Patient:            {key.patient_name}",
            f"Member ID:          {key.member_id}",
            f"Date(s) of service: {_service_dates(key, us_date)}",
            f"Note date:          {us_date(key.note_date)}",
            f"Diagnosis codes:    {_codes(key.diagnosis_codes, ' ')}",
            f"Procedure codes:    {_codes(key.procedure_codes, ' ')}",
            "",
            "Services were provided for the diagnoses listed above.",
            f"Recorded by: {key.provider_name}",
        ]
    )


def _chart_entry(key: ClinicalNoteAnswerKey) -> str:
    return "\n".join(
        [
            f"CHART ENTRY | {key.provider_name} | {iso_date(key.note_date)}",
            f"Pt: {key.patient_name} (member {key.member_id})",
            f"DOS: {_service_dates(key, iso_date)}",
            f"Dx: {_codes(key.diagnosis_codes, ', ')}",
            f"Px: {_codes(key.procedure_codes, ', ')}",
            f"Seen for the coded conditions above. Entry signed by {key.provider_name}.",
        ]
    )


_RENDERERS: dict[str, Callable[[ClinicalNoteAnswerKey], str]] = {
    "visit_note": _visit_note,
    "encounter_summary": _encounter_summary,
    "chart_entry": _chart_entry,
}
