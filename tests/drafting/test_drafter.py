import json
import traceback
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import EvidenceSource, LlmCall, LlmProvider
from src.db.session import create_session_factory, session_scope
from src.drafting.citation_validator import CitationProblemKind
from src.drafting.drafter import (
    DRAFTING_MAX_OUTPUT_TOKENS,
    MAX_EVIDENCE_MATCHES,
    DraftBlockedError,
    DraftFactError,
    DraftingError,
    DraftParseError,
    DraftPlaceholderError,
    DraftResult,
    DraftTruncatedError,
    build_user_message,
    draft_appeal,
)
from src.drafting.fact_check import FactProblem, FactProblemKind
from src.drafting.prompts import DRAFTING_PURPOSE, DraftingPrompt
from src.drafting.schemas import AppealDraft, Citation, ParagraphKind
from src.extraction.schemas import DenialLetterExtraction
from src.llm.gateway import (
    LlmBudgetExceededError,
    LlmGateway,
    LlmRequest,
    ProviderAnswer,
    ProviderUnavailableError,
)
from src.rag.retrieval import EvidenceMatch

# A month far from today, so a row another test stored with the real clock never counts.
NOW = datetime(2031, 5, 15, 12, 0, tzinfo=UTC)
CAP = Decimal("3.00")
# No real prompt has this version: it marks the rows these tests store, so they can be deleted.
PROMPT_VERSION = "v998"
SYSTEM_TEXT = "Draft the made-up appeal letter."

# All values are made up. No stored document and no real policy text is used.
PATIENT_NAME = "Zebulon Quillfeather"
LETTER_VALUES: dict[str, Any] = {
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
    "reference_number": None,
    "letter_date": "2008-03-20",
    "appeal_deadline": "2008-09-16",
}
FIRST_TEXT = "Blue widgets are covered when a doctor orders them.\nGreen widgets are never covered."
SECOND_TEXT = "A home visit is covered once in every calendar month."
GOOD_QUOTE = "Blue widgets are covered when a doctor orders them."
POLICY_TEXT = "The determination covers the widget that was ordered."
OPENING_TEXT = "I am writing to appeal the denial of claim 123456789012345."


def _letter(**changes: Any) -> DenialLetterExtraction:
    values = {**LETTER_VALUES, **changes}
    return DenialLetterExtraction.model_validate(
        {name: {"value": value, "confidence": 0.37} for name, value in values.items()}
    )


def _match(position: int, text: str) -> EvidenceMatch:
    return EvidenceMatch(
        source=EvidenceSource.CMS_NCD,
        source_document_id=str(900 + position),
        chunk_index=position + 3,
        text_sha256=f"{position + 1:064x}",
        section_number=f"999.{position}",
        document_title=f"Made-up determination {position}",
        section_title="Made-up section" if position == 0 else None,
        text=text,
        similarity=0.5,
    )


MATCHES = [_match(0, FIRST_TEXT), _match(1, SECOND_TEXT)]


def _answer(*citations: tuple[str, str]) -> str:
    return json.dumps(
        {
            "paragraphs": [
                {"kind": "other", "text": OPENING_TEXT, "citations": []},
                {
                    "kind": "policy",
                    "text": POLICY_TEXT,
                    "citations": [{"label": label, "quote": quote} for label, quote in citations],
                },
            ]
        }
    )


GOOD_ANSWER = _answer(("E1", GOOD_QUOTE))


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
            input_tokens=2500,
            output_tokens=800,
            cost_usd=Decimal("0.000855"),
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


PROMPT = DraftingPrompt(version=PROMPT_VERSION, system=SYSTEM_TEXT)


def _stored_calls(factory: sessionmaker[Session]) -> int:
    with session_scope(factory) as reader:
        return reader.execute(
            select(func.count())
            .select_from(LlmCall)
            .where(LlmCall.prompt_version == PROMPT_VERSION)
        ).scalar_one()


def _draft(factory: sessionmaker[Session], provider: StubProvider) -> DraftResult:
    return draft_appeal(_gateway(factory, provider), PROMPT, _letter(), MATCHES)


def test_a_good_answer_comes_back_as_a_checked_letter(
    session_factory: sessionmaker[Session],
) -> None:
    result = _draft(session_factory, StubProvider(GOOD_ANSWER))

    assert isinstance(result, DraftResult)
    opening, policy = result.letter.paragraphs
    assert (opening.kind, opening.text, opening.citations) == (
        ParagraphKind.OTHER,
        OPENING_TEXT,
        (),
    )
    assert (policy.kind, policy.text) == (ParagraphKind.POLICY, POLICY_TEXT)
    assert len(policy.citations) == 1


def test_a_label_becomes_the_four_citation_fields_of_its_match(
    session_factory: sessionmaker[Session],
) -> None:
    answer = _answer(("E2", "covered once in every calendar month"), ("E1", GOOD_QUOTE))

    result = _draft(session_factory, StubProvider(answer))

    assert result.letter.paragraphs[1].citations == (
        Citation(
            source=EvidenceSource.CMS_NCD,
            source_document_id="901",
            chunk_index=4,
            text_sha256=f"{2:064x}",
            quote="covered once in every calendar month",
        ),
        Citation(
            source=EvidenceSource.CMS_NCD,
            source_document_id="900",
            chunk_index=3,
            text_sha256=f"{1:064x}",
            quote=GOOD_QUOTE,
        ),
    )


def test_a_citation_has_the_four_fields_and_the_quote_and_no_row_id() -> None:
    assert set(Citation.model_fields) == {
        "source",
        "source_document_id",
        "chunk_index",
        "text_sha256",
        "quote",
    }


def test_the_result_says_who_produced_the_letter(session_factory: sessionmaker[Session]) -> None:
    result = _draft(session_factory, StubProvider(GOOD_ANSWER, model_name="example-drafter"))

    assert result.provider is LlmProvider.PRIMARY
    assert result.model_name == "example-drafter"
    assert result.prompt_version == PROMPT_VERSION


def test_the_call_goes_through_the_gateway_with_the_drafting_labels(
    session_factory: sessionmaker[Session],
) -> None:
    provider = StubProvider(GOOD_ANSWER)

    _draft(session_factory, provider)

    (request,) = provider.requests
    assert request.system == SYSTEM_TEXT
    assert request.purpose == DRAFTING_PURPOSE == "drafting"
    assert request.prompt_version == PROMPT_VERSION
    assert request.max_tokens == DRAFTING_MAX_OUTPUT_TOKENS == 1500
    assert request.response_schema == AppealDraft.model_json_schema()
    assert request.user == build_user_message(_letter(), MATCHES)
    assert _stored_calls(session_factory) == 1


def test_the_model_is_sent_the_letter_values_without_confidence_scores() -> None:
    message = json.loads(build_user_message(_letter(), MATCHES))

    assert set(message) == {"denial_letter", "evidence"}
    assert message["denial_letter"] == LETTER_VALUES
    assert "confidence" not in build_user_message(_letter(), MATCHES)
    assert "0.37" not in build_user_message(_letter(), MATCHES)


def test_the_model_is_sent_each_match_with_its_label_by_position() -> None:
    message = json.loads(build_user_message(_letter(), MATCHES))

    assert message["evidence"] == [
        {
            "label": "E1",
            "document_title": "Made-up determination 0",
            "section_number": "999.0",
            "section_title": "Made-up section",
            "text": FIRST_TEXT,
        },
        {
            "label": "E2",
            "document_title": "Made-up determination 1",
            "section_number": "999.1",
            "section_title": None,
            "text": SECOND_TEXT,
        },
    ]


def test_the_model_is_not_sent_the_fields_that_name_a_chunk() -> None:
    message = build_user_message(_letter(), MATCHES)

    for name in ("text_sha256", "source_document_id", "chunk_index", "similarity"):
        assert name not in message
    assert f"{1:064x}" not in message


def test_text_that_is_not_ascii_is_sent_as_it_is() -> None:
    message = build_user_message(_letter(patient_name="Zoë Ångström"), [_match(0, "naïve – café")])

    assert "Zoë Ångström" in message
    assert "naïve – café" in message


def test_with_no_match_the_evidence_list_is_empty() -> None:
    assert json.loads(build_user_message(_letter(), []))["evidence"] == []


def test_more_matches_than_there_are_labels_are_refused() -> None:
    matches = [_match(position, SECOND_TEXT) for position in range(MAX_EVIDENCE_MATCHES + 1)]

    assert json.loads(build_user_message(_letter(), matches[:-1]))["evidence"][-1]["label"] == "E99"
    with pytest.raises(ValueError, match="at most 99 matches, got 100"):
        build_user_message(_letter(), matches)


def test_a_letter_with_no_policy_paragraph_passes_with_no_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    answer = json.dumps({"paragraphs": [{"kind": "other", "text": OPENING_TEXT}]})

    result = draft_appeal(_gateway(session_factory, StubProvider(answer)), PROMPT, _letter(), [])

    assert [paragraph.citations for paragraph in result.letter.paragraphs] == [()]


def test_an_answer_cut_off_at_the_output_limit_is_an_error(
    session_factory: sessionmaker[Session],
) -> None:
    provider = StubProvider(GOOD_ANSWER, stop_reason="length")

    with pytest.raises(DraftTruncatedError, match="1500 tokens"):
        _draft(session_factory, provider)

    # The call was made and paid for: it is recorded, and there is no second call.
    assert len(provider.requests) == 1
    assert _stored_calls(session_factory) == 1


@pytest.mark.parametrize(
    "answer",
    [
        "Here is the letter: Dear Sir or Madam",
        "",
        '{"paragraphs": [',
        "[]",
        '{"paragraphs": []}',
        '{"letter": "Dear Sir or Madam"}',
        json.dumps({"paragraphs": [{"kind": "legal", "text": OPENING_TEXT}]}),
        json.dumps({"paragraphs": [{"kind": "other", "text": "  "}]}),
        # A citation on a paragraph of kind `other`.
        json.dumps(
            {
                "paragraphs": [
                    {
                        "kind": "other",
                        "text": OPENING_TEXT,
                        "citations": [{"label": "E1", "quote": GOOD_QUOTE}],
                    }
                ]
            }
        ),
        # A label that is not `E` and a position.
        _answer(("the first passage", GOOD_QUOTE)),
        # The model must not write the fields that name a chunk.
        json.dumps(
            {
                "paragraphs": [
                    {
                        "kind": "policy",
                        "text": POLICY_TEXT,
                        "citations": [
                            {"label": "E1", "quote": GOOD_QUOTE, "text_sha256": f"{1:064x}"}
                        ],
                    }
                ]
            }
        ),
    ],
)
def test_an_answer_that_is_not_a_draft_is_an_error_with_no_repair(
    session_factory: sessionmaker[Session], answer: str
) -> None:
    provider = StubProvider(answer)

    with pytest.raises(DraftParseError, match="not JSON or does not fit"):
        _draft(session_factory, provider)

    assert len(provider.requests) == 1


def test_a_policy_paragraph_with_no_citation_blocks_the_draft(
    session_factory: sessionmaker[Session],
) -> None:
    provider = StubProvider(_answer())

    with pytest.raises(DraftBlockedError) as raised:
        _draft(session_factory, provider)

    assert [(problem.kind, problem.paragraph_index) for problem in raised.value.problems] == [
        (CitationProblemKind.POLICY_WITHOUT_CITATION, 1)
    ]
    assert len(provider.requests) == 1


def test_a_blocked_draft_carries_every_problem_and_names_the_kinds(
    session_factory: sessionmaker[Session],
) -> None:
    answer = _answer(
        ("E1", "Green widgets are always covered."),
        ("E2", "Green widgets are never covered."),
        ("E7", "is covered"),
    )

    with pytest.raises(DraftBlockedError) as raised:
        _draft(session_factory, StubProvider(answer))

    assert [(problem.kind, problem.label) for problem in raised.value.problems] == [
        (CitationProblemKind.QUOTE_NOT_IN_EVIDENCE, "E1"),
        (CitationProblemKind.QUOTE_NOT_IN_EVIDENCE, "E2"),
        (CitationProblemKind.QUOTE_TOO_SHORT, "E7"),
        (CitationProblemKind.UNKNOWN_LABEL, "E7"),
    ]
    assert str(raised.value) == (
        "the draft is blocked by the citation validator: "
        "quote_not_in_evidence x2, quote_too_short x1, unknown_label x1"
    )


def test_a_quote_from_evidence_the_model_was_not_shown_blocks_the_draft(
    session_factory: sessionmaker[Session],
) -> None:
    # The quote is real text of the second match, but only the first was retrieved.
    answer = _answer(("E2", "covered once in every calendar month"))

    with pytest.raises(DraftBlockedError) as raised:
        draft_appeal(
            _gateway(session_factory, StubProvider(answer)), PROMPT, _letter(), MATCHES[:1]
        )

    assert [problem.kind for problem in raised.value.problems] == [
        CitationProblemKind.UNKNOWN_LABEL
    ]


def _answer_with_closing(closing: str, *citations: tuple[str, str]) -> str:
    answer = json.loads(_answer(*citations))
    answer["paragraphs"].append({"kind": "other", "text": closing, "citations": []})
    return json.dumps(answer)


def test_a_draft_with_text_in_square_brackets_is_refused_with_no_repair(
    session_factory: sessionmaker[Session],
) -> None:
    provider = StubProvider(_answer_with_closing("Sincerely, [Your Name]", ("E1", GOOD_QUOTE)))

    with pytest.raises(DraftPlaceholderError) as raised:
        _draft(session_factory, provider)

    assert raised.value.paragraph_indexes == (2,)
    assert str(raised.value) == "the draft holds text in square brackets in 1 paragraph(s)"
    # The call was made and paid for: it is recorded, and there is no second call.
    assert len(provider.requests) == 1
    assert _stored_calls(session_factory) == 1


def test_a_draft_with_no_square_bracket_passes_the_placeholder_check(
    session_factory: sessionmaker[Session],
) -> None:
    answer = _answer_with_closing(f"Sincerely, {PATIENT_NAME}", ("E1", GOOD_QUOTE))

    result = _draft(session_factory, StubProvider(answer))

    assert [paragraph.text for paragraph in result.letter.paragraphs][-1] == (
        f"Sincerely, {PATIENT_NAME}"
    )


def test_a_square_bracket_inside_a_quote_alone_does_not_refuse_the_draft(
    session_factory: sessionmaker[Session],
) -> None:
    bracket_text = "Blue widgets [see the made-up section 2] are covered when ordered."
    quote = "Blue widgets [see the made-up section 2] are covered"

    result = draft_appeal(
        _gateway(session_factory, StubProvider(_answer(("E1", quote)))),
        PROMPT,
        _letter(),
        [_match(0, bracket_text)],
    )

    assert result.letter.paragraphs[1].citations[0].quote == quote


def test_a_draft_that_is_blocked_and_has_a_placeholder_is_reported_as_blocked(
    session_factory: sessionmaker[Session],
) -> None:
    answer = _answer_with_closing("Sincerely, [Your Name]")

    with pytest.raises(DraftBlockedError):
        _draft(session_factory, StubProvider(answer))


def test_a_draft_with_a_wrong_amount_and_other_as_the_reason_is_refused_with_no_repair(
    session_factory: sessionmaker[Session],
) -> None:
    closing = "I appeal $999.99, denied for the reason 'other'."
    provider = StubProvider(_answer_with_closing(closing, ("E1", GOOD_QUOTE)))

    with pytest.raises(DraftFactError) as raised:
        _draft(session_factory, provider)

    assert raised.value.problems == (
        FactProblem(kind=FactProblemKind.AMOUNT_NOT_A_DENIED_LINE, paragraph_index=2),
        FactProblem(kind=FactProblemKind.OTHER_AS_REASON, paragraph_index=2),
    )
    assert str(raised.value) == (
        "the draft is refused by the fact check: amount_not_a_denied_line x1, other_as_reason x1"
    )
    # The call was made and paid for: it is recorded, and there is no second call.
    assert len(provider.requests) == 1
    assert _stored_calls(session_factory) == 1


def test_a_draft_that_names_the_amounts_of_its_denied_line_passes_the_fact_check(
    session_factory: sessionmaker[Session],
) -> None:
    closing = "Line 2 was allowed at $130.10 and paid $0, with no other reason given."
    answer = _answer_with_closing(closing, ("E1", GOOD_QUOTE))

    result = _draft(session_factory, StubProvider(answer))

    assert result.letter.paragraphs[-1].text == closing


def test_an_amount_or_the_word_other_inside_a_quote_alone_does_not_refuse_the_draft(
    session_factory: sessionmaker[Session],
) -> None:
    evidence_text = "A made-up fee of $999.99 applies when the category is 'other' in the list."
    quote = "fee of $999.99 applies when the category is 'other' in"

    result = draft_appeal(
        _gateway(session_factory, StubProvider(_answer(("E1", quote)))),
        PROMPT,
        _letter(),
        [_match(0, evidence_text)],
    )

    assert result.letter.paragraphs[1].citations[0].quote == quote


def test_a_draft_that_is_blocked_and_has_a_wrong_fact_is_reported_as_blocked(
    session_factory: sessionmaker[Session],
) -> None:
    answer = _answer_with_closing("I appeal $999.99.")

    with pytest.raises(DraftBlockedError):
        _draft(session_factory, StubProvider(answer))


def test_a_draft_with_a_placeholder_and_a_wrong_fact_is_reported_for_the_placeholder(
    session_factory: sessionmaker[Session],
) -> None:
    answer = _answer_with_closing("I appeal $999.99. [Your Name]", ("E1", GOOD_QUOTE))

    with pytest.raises(DraftPlaceholderError):
        _draft(session_factory, StubProvider(answer))


@pytest.mark.parametrize(
    ("answer", "stop_reason", "error_type"),
    [
        (GOOD_ANSWER, "length", DraftTruncatedError),
        (
            _answer_with_closing(
                f"{PATIENT_NAME} wants to appeal the denial of $999.99", ("E1", GOOD_QUOTE)
            ),
            "stop",
            DraftFactError,
        ),
        (
            _answer_with_closing(f"[{PATIENT_NAME}] appeal the denial", ("E1", GOOD_QUOTE)),
            "stop",
            DraftPlaceholderError,
        ),
        (f"not json, about {PATIENT_NAME}: {GOOD_QUOTE}", "stop", DraftParseError),
        (
            json.dumps({"paragraphs": [{"kind": PATIENT_NAME, "text": GOOD_QUOTE}]}),
            "stop",
            DraftParseError,
        ),
        (_answer(("E1", f"{PATIENT_NAME} always gets green widgets.")), "stop", DraftBlockedError),
    ],
)
def test_no_error_shows_the_answer_the_letter_or_the_evidence(
    session_factory: sessionmaker[Session],
    answer: str,
    stop_reason: str,
    error_type: type[DraftingError],
) -> None:
    with pytest.raises(error_type) as raised:
        _draft(session_factory, StubProvider(answer, stop_reason=stop_reason))

    shown = "".join(traceback.format_exception(raised.value)) + repr(raised.value)
    assert isinstance(raised.value, DraftingError)
    for secret in (PATIENT_NAME, "widgets", "MBR-000001", "appeal the denial"):
        assert secret not in shown


def test_the_result_shows_no_letter_text_and_no_quote(
    session_factory: sessionmaker[Session],
) -> None:
    shown = repr(_draft(session_factory, StubProvider(GOOD_ANSWER)))

    assert "example-small-model" in shown
    assert "widgets" not in shown
    assert "appeal the denial" not in shown


def test_a_call_that_does_not_fit_the_budget_drafts_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    provider = StubProvider(GOOD_ANSWER, highest_cost="3.01")

    with pytest.raises(LlmBudgetExceededError):
        _draft(session_factory, provider)

    assert provider.requests == []
    assert _stored_calls(session_factory) == 0


def test_the_fallback_drafts_when_the_primary_is_unavailable(
    session_factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(GOOD_ANSWER, error=ProviderUnavailableError("made-up outage"))
    fallback = StubProvider(GOOD_ANSWER, model_name="example-open-model")

    result = draft_appeal(_gateway(session_factory, primary, fallback), PROMPT, _letter(), MATCHES)

    assert result.provider is LlmProvider.FALLBACK
    assert result.model_name == "example-open-model"
    assert len(result.letter.paragraphs[1].citations) == 1
