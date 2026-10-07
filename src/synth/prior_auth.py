"""Prior-authorisation record generator v1: a fabricated record for some denied claims.

Every record is made up. The claims sample has no prior-authorisation field at all, so the
authorisation number, the request date, the decision date and the status are invented by this
module. The dates of service and the diagnosis and procedure codes come from the synthetic
claims sample; the patient name, member id, provider and payer come from `src.synth.identity`,
so the record describes the same people as the claim's denial letter and clinical note. No
real patient, provider or insurer appears in a record.

The record is the payer's note of a request made before the service:

- Only some claims get one. `needs_prior_auth` is the rule: a denied claim whose headline
  reason is `medical_necessity` or `noncovered`.
- **The status is a seed draw only**, `approved` or `denied`, half each. It never reads the
  appeal-success proxy, its chance, the amount band or the split, so it says nothing about
  whether an appeal would win. Nobody should read "approved" as a real ground for appeal.
- It never shows money, the claim number or policy text, and never the proxy, its chance, the
  amount band or the split.

How one record is made:

- Every choice of its own is a repeatable draw of the text
  `doc:<generator version>:<seed>:prior_auth:<claim id>:<name of the choice>`.
- The answer key (every value the record prints) is built first. A template then writes the
  text from the answer key alone, so the text cannot hold a value the key lacks.

Any change to a template, a rule, a date range or the draw text needs a new
`GENERATOR_VERSION`, because the same seed would no longer give the same text.
"""

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DenialReasonCategory, DocumentType
from src.ml.labels import CATEGORY_BY_INDICATOR, ClaimId, ClaimLabel, LabelLine, is_denied_line
from src.synth.formats import (
    CODE_NOT_PROVIDED,
    DocumentClaim,
    iso_date,
    long_date,
    service_dates,
    us_date,
)
from src.synth.identity import (
    MAX_SEED,
    check_seed,
    claim_identity,
    document_draw,
    made_up_code,
    pick,
)

GENERATOR_VERSION = "v1"
DOCUMENT_TYPE = DocumentType.PRIOR_AUTH

TEMPLATE_IDS = ("authorization_notice", "request_summary", "status_record")

# A claim gets a record when its headline reason is one of these; a denied line is listed on
# the record when its own reason is one of these.
PRIOR_AUTH_CATEGORIES = frozenset(
    {DenialReasonCategory.MEDICAL_NECESSITY, DenialReasonCategory.NONCOVERED}
)

# All made up. The request is dated this many days before the first service date, and the
# decision this many days after the request (both ends included). The longest decision delay
# is shorter than the shortest lead, so the decision is always before the service.
MIN_REQUEST_LEAD_DAYS = 7
MAX_REQUEST_LEAD_DAYS = 30
MIN_DECISION_DELAY_DAYS = 1
MAX_DECISION_DELAY_DAYS = 5
# An assumption, not a measurement: half of the records are approved.
APPROVED_SHARE = Fraction(1, 2)

AUTHORIZATION_PREFIX = "PA-"
AUTHORIZATION_DIGITS = 8


class PriorAuthStatus(StrEnum):
    """The payer's made-up decision on the request."""

    APPROVED = "approved"
    DENIED = "denied"


class PriorAuthLine(LabelLine, Protocol):
    """A service line: what the label rule reads, plus the procedure code."""

    @property
    def hcpcs_code(self) -> str | None: ...


class PriorAuthAnswerKey(BaseModel):
    """Every value printed in one prior-authorisation record, real or fabricated.

    `requested_procedure_codes` has one entry per listed line, in line order: the denied lines
    whose reason is `medical_necessity` or `noncovered`. An entry is None when the source has
    no procedure code; the record then says "not provided", as it does when `diagnosis_codes`
    is empty. The two service dates are the claim's, printed as the planned dates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    service_from_date: date
    service_thru_date: date
    diagnosis_codes: tuple[str, ...]
    requested_procedure_codes: tuple[str | None, ...] = Field(min_length=1)
    patient_name: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)
    payer_name: str = Field(min_length=1)
    authorization_number: str = Field(min_length=1)
    request_date: date
    decision_date: date
    status: PriorAuthStatus


class PriorAuthRecord(BaseModel):
    """One generated prior-authorisation record: the fields of a `generated_documents` row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    document_type: DocumentType
    template_id: str = Field(min_length=1, max_length=40)
    seed: int = Field(ge=0, le=MAX_SEED)
    generator_version: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1)
    answer_key: PriorAuthAnswerKey


def needs_prior_auth(label: ClaimLabel) -> bool:
    """Whether a claim gets a prior-authorisation record: it is denied and its headline reason
    is `medical_necessity` or `noncovered`."""
    return label.is_denied and label.denial_reason_category in PRIOR_AUTH_CATEGORIES


def generate_prior_auth(
    claim: DocumentClaim, lines: Sequence[PriorAuthLine], label: ClaimLabel, seed: int
) -> PriorAuthRecord:
    """Write the prior-authorisation record of one claim that `needs_prior_auth`.

    `lines` are all of the claim's lines, in any order, and `label` is that claim's label. The
    label only decides whether the claim gets a record; nothing of it is printed. The same
    claim, seed and `GENERATOR_VERSION` always give the same record.
    """
    claim_id = claim.source_claim_id
    check_seed(seed)
    if label.source_claim_id != claim_id:
        raise ValueError(f"the label is for claim {label.source_claim_id}, not claim {claim_id}")
    if not needs_prior_auth(label):
        raise ValueError(f"claim {claim_id} gets no prior-authorisation record")
    requested = sorted(filter(_is_requested_line, lines), key=lambda line: line.line_number)
    if not requested:
        raise ValueError(
            f"claim {claim_id} is labelled for a prior-authorisation record but has no line "
            "that qualifies"
        )

    def draw(choice: str) -> Fraction:
        return document_draw(GENERATOR_VERSION, seed, DOCUMENT_TYPE, claim_id, choice)

    request_date = claim.claim_from_date - timedelta(
        days=_days(draw("request_lead_days"), MIN_REQUEST_LEAD_DAYS, MAX_REQUEST_LEAD_DAYS)
    )
    decision_date = request_date + timedelta(
        days=_days(draw("decision_delay_days"), MIN_DECISION_DELAY_DAYS, MAX_DECISION_DELAY_DAYS)
    )
    identity = claim_identity(claim_id, seed)
    key = PriorAuthAnswerKey(
        service_from_date=claim.claim_from_date,
        service_thru_date=claim.claim_thru_date,
        diagnosis_codes=tuple(claim.diagnosis_codes),
        requested_procedure_codes=tuple(line.hcpcs_code or None for line in requested),
        patient_name=identity.patient_name,
        member_id=identity.member_id,
        provider_name=identity.provider_name,
        payer_name=identity.payer_name,
        authorization_number=AUTHORIZATION_PREFIX
        + made_up_code(draw("authorization_number"), 0, AUTHORIZATION_DIGITS),
        request_date=request_date,
        decision_date=decision_date,
        status=(
            PriorAuthStatus.APPROVED if draw("status") < APPROVED_SHARE else PriorAuthStatus.DENIED
        ),
    )
    template_id = pick(TEMPLATE_IDS, draw("template"))
    return PriorAuthRecord(
        source_claim_id=claim_id,
        document_type=DOCUMENT_TYPE,
        template_id=template_id,
        seed=seed,
        generator_version=GENERATOR_VERSION,
        text=_RENDERERS[template_id](key),
        answer_key=key,
    )


def _is_requested_line(line: PriorAuthLine) -> bool:
    category = CATEGORY_BY_INDICATOR.get(line.processing_indicator, DenialReasonCategory.OTHER)
    return is_denied_line(line) and category in PRIOR_AUTH_CATEGORIES


def _days(draw: Fraction, lowest: int, highest: int) -> int:
    return lowest + int(draw * (highest - lowest + 1))


def _service_dates(key: PriorAuthAnswerKey, show: Callable[[date], str]) -> str:
    return service_dates(key.service_from_date, key.service_thru_date, show)


def _requested(key: PriorAuthAnswerKey) -> list[str]:
    return [code or CODE_NOT_PROVIDED for code in key.requested_procedure_codes]


def _diagnoses(key: PriorAuthAnswerKey, separator: str) -> str:
    return separator.join(key.diagnosis_codes) or CODE_NOT_PROVIDED


def _authorization_notice(key: PriorAuthAnswerKey) -> str:
    return "\n".join(
        [
            key.payer_name,
            "Prior Authorization Department",
            "",
            long_date(key.decision_date),
            "",
            f"Authorization number: {key.authorization_number}",
            "",
            f"Requesting provider: {key.provider_name}",
            f"Patient: {key.patient_name}",
            f"Member ID: {key.member_id}",
            "",
            f"On {long_date(key.request_date)} we received a request for prior authorization "
            f"of services planned for {_service_dates(key, long_date)}. The diagnosis codes on "
            f"the request are {_diagnoses(key, ', ')}.",
            "",
            "Procedure codes requested:",
            "",
            *[f"- {code}" for code in _requested(key)],
            "",
            f"Decision: this request was {key.status.value}.",
            "",
            "Prior Authorization Department",
            key.payer_name,
        ]
    )


def _request_summary(key: PriorAuthAnswerKey) -> str:
    return "\n".join(
        [
            "PRIOR AUTHORIZATION REQUEST SUMMARY",
            key.payer_name,
            "",
            f"Authorization no.:        {key.authorization_number}",
            f"Status:                   {key.status.value.upper()}",
            f"Member:                   {key.patient_name}",
            f"Member ID:                {key.member_id}",
            f"Requesting provider:      {key.provider_name}",
            f"Planned service date(s):  {_service_dates(key, us_date)}",
            f"Request received:         {us_date(key.request_date)}",
            f"Decision date:            {us_date(key.decision_date)}",
            f"Diagnosis codes:          {_diagnoses(key, ' ')}",
            f"Procedure codes:          {'; '.join(_requested(key))}",
        ]
    )


def _status_record(key: PriorAuthAnswerKey) -> str:
    return "\n".join(
        [
            f"PRIOR AUTH RECORD | {key.payer_name} | {key.authorization_number}",
            f"Pt: {key.patient_name} (member {key.member_id}) | Provider: {key.provider_name}",
            f"Planned DOS: {_service_dates(key, iso_date)}",
            f"Requested: {iso_date(key.request_date)} | Decided: "
            f"{iso_date(key.decision_date)} | Status: {key.status.value}",
            f"Dx: {_diagnoses(key, ', ')}",
            f"Px requested: {', '.join(_requested(key))}",
        ]
    )


_RENDERERS: dict[str, Callable[[PriorAuthAnswerKey], str]] = {
    "authorization_notice": _authorization_notice,
    "request_summary": _request_summary,
    "status_record": _status_record,
}
