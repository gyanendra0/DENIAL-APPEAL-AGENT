"""Made-up claims and documents for the extraction tests, and a stub provider.

The rows are really committed (the code under test commits each result on its own), so
`stored_documents` removes them afterwards. No stored document and no real value is used.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    ClaimSample,
    ClaimSampleLabel,
    DatasetSplit,
    DenialReasonCategory,
    DocumentType,
    GeneratedDocument,
    LlmCall,
)
from src.db.session import create_session_factory, session_scope
from src.extraction.prompts import ExtractionPrompt
from src.llm.gateway import LlmGateway, LlmRequest, ProviderAnswer

LETTER = DocumentType.DENIAL_LETTER
NOTE = DocumentType.CLINICAL_NOTE
PRIOR_AUTH = DocumentType.PRIOR_AUTH

# No real prompt has this version: it marks the rows these tests store, so they can be deleted.
PROMPT_VERSION = "v999"
CAP = Decimal("3.00")
CALL_COST = Decimal("0.000123")
MODEL_NAME = "example-small-model"

# Two claims in the validation split and one in the test split; only the first has a
# prior-authorisation record. In claim id order the validation split is: letter, note and
# record of claim 1, then letter and note of claim 2.
CLAIM_1 = "810000000000001"
CLAIM_2 = "810000000000002"
CLAIM_3 = "810000000000003"
SPLITS = {
    CLAIM_1: DatasetSplit.VALIDATION,
    CLAIM_2: DatasetSplit.VALIDATION,
    CLAIM_3: DatasetSplit.TEST,
}
DOCUMENT_TYPES = {
    CLAIM_1: (LETTER, NOTE, PRIOR_AUTH),
    CLAIM_2: (LETTER, NOTE),
    CLAIM_3: (LETTER, NOTE),
}
VALIDATION_KEYS = [
    (CLAIM_1, LETTER),
    (CLAIM_1, NOTE),
    (CLAIM_1, PRIOR_AUTH),
    (CLAIM_2, LETTER),
    (CLAIM_2, NOTE),
]
PATIENT_NAME = "Zebulon Quillfeather"
# Every document of a type has the same made-up answer key, as plain JSON values.
ANSWER_KEYS: dict[DocumentType, dict[str, Any]] = {
    LETTER: {
        "claim_number": "123456789012345",
        "service_from_date": "2008-03-01",
        "service_thru_date": "2008-03-02",
        "diagnosis_codes": ["4011", "25000"],
        "denied_lines": [
            {
                "line_number": 2,
                "hcpcs_code": "99213",
                "allowed_charge_amount": "130.10",
                "payment_amount": "0.00",
                "reason_category": "noncovered",
            }
        ],
        "total_allowed_charge_amount": "130.10",
        "total_payment_amount": "0.00",
        "denial_reason_category": "noncovered",
        "patient_name": PATIENT_NAME,
        "member_id": "MBR-000001",
        "provider_name": "Made Up Practice",
        "payer_name": "Made Up Health Plan",
        "reference_number": "REF-000001",
        "letter_date": "2008-03-20",
        "appeal_deadline": "2008-09-16",
    },
    NOTE: {
        "service_from_date": "2008-03-01",
        "service_thru_date": "2008-03-02",
        "note_date": "2008-03-02",
        "diagnosis_codes": ["4011"],
        "procedure_codes": ["99213"],
        "patient_name": PATIENT_NAME,
        "member_id": "MBR-000001",
        "provider_name": "Made Up Practice",
    },
    PRIOR_AUTH: {
        "service_from_date": "2008-03-01",
        "service_thru_date": "2008-03-02",
        "diagnosis_codes": ["4011"],
        "requested_procedure_codes": ["99213", None],
        "patient_name": PATIENT_NAME,
        "member_id": "MBR-000001",
        "provider_name": "Made Up Practice",
        "payer_name": "Made Up Health Plan",
        "authorization_number": "AUTH-000001",
        "request_date": "2008-02-10",
        "decision_date": "2008-02-14",
        "status": "approved",
    },
}


def document_text(claim_id: str, document_type: DocumentType) -> str:
    return f"Made-up {document_type.value} of claim {claim_id} about {PATIENT_NAME}."


def system_text(document_type: DocumentType) -> str:
    return f"Extract the fields of the made-up {document_type.value}."


def prompts(version: str = PROMPT_VERSION) -> dict[DocumentType, ExtractionPrompt]:
    return {
        kind: ExtractionPrompt(version=version, document_type=kind, system=system_text(kind))
        for kind in DocumentType
    }


def fields_json(document_type: DocumentType, **changed: Any) -> dict[str, Any]:
    """The answer key of `document_type` as extraction fields, with `changed` values swapped in."""
    values = {**ANSWER_KEYS[document_type], **changed}
    return {name: {"value": value, "confidence": 0.9} for name, value in values.items()}


def right_answer(request: LlmRequest) -> str:
    """The answer a model that reads perfectly would give: the answer key of the type asked for."""
    for kind in DocumentType:
        if request.system == system_text(kind):
            return json.dumps(fields_json(kind))
    raise AssertionError("a request with an unknown system prompt")


class StubProvider:
    """A provider that answers from a function and remembers the requests it was sent."""

    def __init__(
        self,
        answer: Callable[[LlmRequest], str] = right_answer,
        *,
        stop_reason: Callable[[LlmRequest], str] = lambda request: "stop",
        highest_cost: str = "0.01",
        error: Callable[[LlmRequest], Exception | None] = lambda request: None,
        model_name: str = MODEL_NAME,
    ) -> None:
        self._answer = answer
        self._stop_reason = stop_reason
        self._highest_cost = Decimal(highest_cost)
        self._error = error
        self._model_name = model_name
        self.requests: list[LlmRequest] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def highest_cost(self, request: LlmRequest) -> Decimal:
        return self._highest_cost

    def run(self, request: LlmRequest) -> ProviderAnswer:
        self.requests.append(request)
        error = self._error(request)
        if error is not None:
            raise error
        return ProviderAnswer(
            text=self._answer(request),
            input_tokens=520,
            output_tokens=75,
            cost_usd=CALL_COST,
            stop_reason=self._stop_reason(request),
        )


def gateway(
    factory: sessionmaker[Session], primary: StubProvider, fallback: StubProvider | None = None
) -> LlmGateway:
    return LlmGateway(primary=primary, fallback=fallback, session_factory=factory, cap_usd=CAP)


def is_about(claim_id: str) -> Callable[[LlmRequest], bool]:
    """A check that a request carries a document of `claim_id`."""
    return lambda request: f"claim {claim_id}" in request.user


@contextmanager
def stored_documents(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Commit the made-up claims, labels and documents; remove them and the tests' calls after."""
    factory = create_session_factory(engine)
    _remove(factory)  # in case an earlier run was killed before its clean-up
    with session_scope(factory) as session:
        for claim_id, split in SPLITS.items():
            session.add(
                ClaimSample(
                    source_claim_id=claim_id,
                    claim_from_date=date(2008, 3, 1),
                    claim_thru_date=date(2008, 3, 2),
                    diagnosis_codes=["4011"],
                )
            )
            session.flush()
            session.add(
                ClaimSampleLabel(
                    source_claim_id=claim_id,
                    is_denied=True,
                    denial_reason_category=DenialReasonCategory.NONCOVERED,
                    appeal_success_proxy=False,
                    label_rule_version="v2",
                    split=split,
                    split_seed=42,
                )
            )
            for kind in DOCUMENT_TYPES[claim_id]:
                session.add(
                    GeneratedDocument(
                        source_claim_id=claim_id,
                        document_type=kind,
                        template_id="made_up_template",
                        seed=42,
                        generator_version="v1",
                        text=document_text(claim_id, kind),
                        answer_key=ANSWER_KEYS[kind],
                        noise_version="v1",
                        noise_record={
                            "level": "none",
                            "swaps": 0,
                            "drops": 0,
                            "missing_field": None,
                            "page_width": None,
                        },
                    )
                )
    try:
        yield factory
    finally:
        _remove(factory)


def _remove(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as session:
        # Labels, documents and extraction results go with their claim.
        session.execute(delete(ClaimSample).where(ClaimSample.source_claim_id.in_(SPLITS)))
        session.execute(delete(LlmCall).where(LlmCall.prompt_version == PROMPT_VERSION))
