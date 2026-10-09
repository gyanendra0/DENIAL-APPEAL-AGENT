import json
import logging
import traceback
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import DenialReasonCategory, DocumentType, LlmCall, LlmProvider
from src.db.session import create_session_factory, session_scope
from src.extraction.extractor import (
    ExtractionError,
    ExtractionParseError,
    ExtractionResult,
    ExtractionTruncatedError,
    extract_document,
)
from src.extraction.prompts import EXTRACTION_PURPOSE, ExtractionPrompt
from src.extraction.schemas import (
    EXTRACTION_MAX_OUTPUT_TOKENS,
    DenialLetterExtraction,
    schema_for,
)
from src.llm.gateway import (
    LlmBudgetExceededError,
    LlmGateway,
    LlmRequest,
    ProviderAnswer,
    ProviderUnavailableError,
)

# A month far from today, so a row another test stored with the real clock never counts.
NOW = datetime(2031, 3, 15, 12, 0, tzinfo=UTC)
CAP = Decimal("3.00")
# No real prompt has this version: it marks the rows these tests store, so they can be deleted.
PROMPT_VERSION = "v999"
SYSTEM_TEXT = "Extract the fields of the made-up document."
# All values are made up. No stored document is used.
DOCUMENT_TEXT = "Made-up document about Zebulon Quillfeather, claim 123456789012345."
PATIENT_NAME = "Zebulon Quillfeather"
VALUES: dict[DocumentType, dict[str, Any]] = {
    DocumentType.DENIAL_LETTER: {
        "claim_number": "123456789012345",
        "service_from_date": "2008-03-01",
        "service_thru_date": "2008-03-02",
        "diagnosis_codes": ["4011", "25000"],
        "denied_lines": [
            {
                "line_number": 2,
                "hcpcs_code": "99213",
                "allowed_charge_amount": 130.10,
                "payment_amount": 0.00,
                "reason_category": "noncovered",
            }
        ],
        "total_allowed_charge_amount": 130.10,
        "total_payment_amount": 0.00,
        "denial_reason_category": "noncovered",
        "patient_name": PATIENT_NAME,
        "member_id": "MBR-000001",
        "provider_name": "Made Up Practice",
        "payer_name": "Made Up Health Plan",
        "reference_number": "REF-000001",
        "letter_date": "2008-03-20",
        "appeal_deadline": "2008-09-16",
    },
    DocumentType.CLINICAL_NOTE: {
        "service_from_date": "2008-03-01",
        "service_thru_date": "2008-03-02",
        "note_date": "2008-03-02",
        "diagnosis_codes": ["4011"],
        "procedure_codes": ["99213"],
        "patient_name": PATIENT_NAME,
        "member_id": "MBR-000001",
        "provider_name": "Made Up Practice",
    },
    DocumentType.PRIOR_AUTH: {
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


def _fields(document_type: DocumentType) -> dict[str, Any]:
    return {
        name: {"value": value, "confidence": 0.9} for name, value in VALUES[document_type].items()
    }


def _answer(document_type: DocumentType = DocumentType.DENIAL_LETTER) -> str:
    return json.dumps(_fields(document_type))


class StubProvider:
    """A provider that answers from memory and remembers the requests it was sent."""

    def __init__(
        self,
        text: str,
        *,
        model_name: str = "example-small-model",
        stop_reason: str = "stop",
        highest_cost: str = "0.01",
        error: Exception | None = None,
    ) -> None:
        self._text = text
        self._model_name = model_name
        self._stop_reason = stop_reason
        self._highest_cost = Decimal(highest_cost)
        self._error = error
        self.requests: list[LlmRequest] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def highest_cost(self, request: LlmRequest) -> Decimal:
        return self._highest_cost

    def run(self, request: LlmRequest) -> ProviderAnswer:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return ProviderAnswer(
            text=self._text,
            input_tokens=520,
            output_tokens=75,
            cost_usd=Decimal("0.000123"),
            stop_reason=self._stop_reason,
        )


@pytest.fixture
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """A factory whose sessions really commit; the rows these tests store are deleted after."""
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        with session_scope(factory) as cleanup:
            cleanup.execute(delete(LlmCall).where(LlmCall.prompt_version == PROMPT_VERSION))


def _gateway(
    factory: sessionmaker[Session], primary: StubProvider, fallback: StubProvider | None = None
) -> LlmGateway:
    return LlmGateway(
        primary=primary,
        fallback=fallback,
        session_factory=factory,
        cap_usd=CAP,
        clock=lambda: NOW,
    )


def _prompt(document_type: DocumentType = DocumentType.DENIAL_LETTER) -> ExtractionPrompt:
    return ExtractionPrompt(version=PROMPT_VERSION, document_type=document_type, system=SYSTEM_TEXT)


def _stored_calls(factory: sessionmaker[Session]) -> int:
    with session_scope(factory) as reader:
        return reader.execute(
            select(func.count())
            .select_from(LlmCall)
            .where(LlmCall.prompt_version == PROMPT_VERSION)
        ).scalar_one()


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_returns_the_typed_fields_of_each_document_type(
    session_factory: sessionmaker[Session], document_type: DocumentType
) -> None:
    provider = StubProvider(_answer(document_type))

    result = extract_document(
        _gateway(session_factory, provider), _prompt(document_type), DOCUMENT_TEXT
    )

    assert isinstance(result, ExtractionResult)
    assert result.document_type is document_type
    assert isinstance(result.fields, schema_for(document_type))
    assert result.fields.patient_name.value == PATIENT_NAME
    assert result.fields.patient_name.confidence == 0.9


def test_values_arrive_as_dates_exact_decimals_and_categories(
    session_factory: sessionmaker[Session],
) -> None:
    result = extract_document(
        _gateway(session_factory, StubProvider(_answer())), _prompt(), DOCUMENT_TEXT
    )

    assert isinstance(result.fields, DenialLetterExtraction)
    assert result.fields.letter_date.value == date(2008, 3, 20)
    assert result.fields.total_allowed_charge_amount.value == Decimal("130.1")
    assert result.fields.denial_reason_category.value is DenialReasonCategory.NONCOVERED


def test_the_result_says_who_answered_and_with_which_prompt(
    session_factory: sessionmaker[Session],
) -> None:
    result = extract_document(
        _gateway(session_factory, StubProvider(_answer())), _prompt(), DOCUMENT_TEXT
    )

    assert result.provider is LlmProvider.PRIMARY
    assert result.model_name == "example-small-model"
    assert result.prompt_version == PROMPT_VERSION


def test_the_result_names_the_fallback_when_it_answered(
    session_factory: sessionmaker[Session],
) -> None:
    primary = StubProvider("", error=ProviderUnavailableError("made-up outage"))
    fallback = StubProvider(_answer(), model_name="example-open-model")

    result = extract_document(
        _gateway(session_factory, primary, fallback), _prompt(), DOCUMENT_TEXT
    )

    assert result.provider is LlmProvider.FALLBACK
    assert result.model_name == "example-open-model"


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_sends_one_request_with_the_prompt_the_text_and_the_types_schema(
    session_factory: sessionmaker[Session], document_type: DocumentType
) -> None:
    provider = StubProvider(_answer(document_type))

    extract_document(_gateway(session_factory, provider), _prompt(document_type), DOCUMENT_TEXT)

    assert provider.requests == [
        LlmRequest(
            system=SYSTEM_TEXT,
            user=DOCUMENT_TEXT,
            max_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
            prompt_version=PROMPT_VERSION,
            purpose=EXTRACTION_PURPOSE,
            response_schema=schema_for(document_type).model_json_schema(),
        )
    ]


def test_the_call_goes_through_the_gateway_so_its_spend_is_recorded(
    session_factory: sessionmaker[Session],
) -> None:
    extract_document(_gateway(session_factory, StubProvider(_answer())), _prompt(), DOCUMENT_TEXT)

    assert _stored_calls(session_factory) == 1


def _without(field: str) -> str:
    fields = _fields(DocumentType.DENIAL_LETTER)
    del fields[field]
    return json.dumps(fields)


def _with(field: str, value: Any) -> str:
    return json.dumps(_fields(DocumentType.DENIAL_LETTER) | {field: value})


BAD_ANSWERS = {
    "not json": f"Here are the fields of {PATIENT_NAME}",
    "empty": "",
    "a code fence": f"```json\n{_answer()}\n```",
    "a missing field": _without("member_id"),
    "an unknown field": _with("billed_amount", {"value": 1, "confidence": 0.9}),
    "a value of the wrong kind": _with("letter_date", {"value": "20 March", "confidence": 0.9}),
    "a bare value": _with("member_id", "MBR-000001"),
    "a confidence above one": _with("member_id", {"value": "MBR-000001", "confidence": 1.5}),
}


@pytest.mark.parametrize("answer", BAD_ANSWERS.values(), ids=BAD_ANSWERS.keys())
def test_an_answer_that_does_not_fit_the_schema_is_an_error_not_a_guess(
    session_factory: sessionmaker[Session], answer: str
) -> None:
    provider = StubProvider(answer)

    with pytest.raises(ExtractionParseError, match="denial_letter"):
        extract_document(_gateway(session_factory, provider), _prompt(), DOCUMENT_TEXT)

    # No second paid call to repair the answer.
    assert len(provider.requests) == 1


def test_an_answer_that_stopped_for_length_is_an_error_even_when_it_would_parse(
    session_factory: sessionmaker[Session],
) -> None:
    provider = StubProvider(_answer(), stop_reason="length")

    with pytest.raises(ExtractionTruncatedError, match="output limit"):
        extract_document(_gateway(session_factory, provider), _prompt(), DOCUMENT_TEXT)

    assert len(provider.requests) == 1


def test_both_errors_are_extraction_errors() -> None:
    assert issubclass(ExtractionParseError, ExtractionError)
    assert issubclass(ExtractionTruncatedError, ExtractionError)


@pytest.mark.parametrize(
    ("answer", "stop_reason"),
    [(_with("letter_date", {"value": PATIENT_NAME, "confidence": 0.9}), "stop"), ("{", "length")],
)
def test_an_error_never_quotes_the_document_or_the_answer(
    session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    answer: str,
    stop_reason: str,
) -> None:
    provider = StubProvider(answer, stop_reason=stop_reason)

    with caplog.at_level(logging.DEBUG), pytest.raises(ExtractionError) as caught:
        extract_document(_gateway(session_factory, provider), _prompt(), DOCUMENT_TEXT)

    # The whole traceback, as a log would print it, with any chained error.
    printed = "".join(traceback.format_exception(caught.value)) + caplog.text
    assert PATIENT_NAME not in printed
    assert "123456789012345" not in printed
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ or caught.value.__context__ is None


def test_a_good_extraction_logs_no_document_text_and_no_value(
    session_factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        extract_document(
            _gateway(session_factory, StubProvider(_answer())), _prompt(), DOCUMENT_TEXT
        )

    assert "llm call:" in caplog.text
    assert PATIENT_NAME not in caplog.text
    assert "MBR-000001" not in caplog.text


def test_a_call_over_the_budget_passes_through_and_calls_no_provider(
    session_factory: sessionmaker[Session],
) -> None:
    provider = StubProvider(_answer(), highest_cost="3.01")

    with pytest.raises(LlmBudgetExceededError):
        extract_document(_gateway(session_factory, provider), _prompt(), DOCUMENT_TEXT)

    assert provider.requests == []
