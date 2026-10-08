import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import LlmCall, LlmProvider
from src.db.session import create_session_factory, session_scope
from src.llm import gateway as gateway_module
from src.llm.budget import month_to_date_spend
from src.llm.gateway import (
    LlmBudgetExceededError,
    LlmGateway,
    LlmRequest,
    LlmResult,
    LlmUnavailableError,
    ProviderAnswer,
    ProviderUnavailableError,
)

# A month far from today, so a row another test stored with the real clock never counts.
NOW = datetime(2031, 3, 15, 12, 0, tzinfo=UTC)
CAP = Decimal("3.00")
ONE_MILLIONTH = Decimal("0.000001")
# Marks the rows these tests store, so they can be deleted afterwards.
PURPOSE = "gateway-test"
PROMPT_TEXT = "Read the made-up letter and name the denial reason."
ANSWER_TEXT = "made-up answer"


class StubProvider:
    """A provider that answers from memory and remembers how often it was called."""

    def __init__(
        self,
        model_name: str,
        *,
        highest_cost: str = "0.01",
        cost: str = "0.000123",
        error: Exception | None = None,
    ) -> None:
        self._model_name = model_name
        self._highest_cost = Decimal(highest_cost)
        self._cost = Decimal(cost)
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
            text=ANSWER_TEXT,
            input_tokens=520,
            output_tokens=75,
            cost_usd=self._cost,
            stop_reason="stop",
        )


def _request(**overrides: Any) -> LlmRequest:
    fields: dict[str, Any] = {
        "system": "You extract fields from made-up documents.",
        "user": PROMPT_TEXT,
        "max_tokens": 200,
        "prompt_version": "extraction-v1",
        "purpose": PURPOSE,
    }
    return LlmRequest(**(fields | overrides))


@pytest.fixture
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """A factory whose sessions really commit; the rows these tests store are deleted after."""
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        with session_scope(factory) as cleanup:
            cleanup.execute(delete(LlmCall).where(LlmCall.purpose == PURPOSE))


def _gateway(
    factory: sessionmaker[Session],
    primary: StubProvider,
    fallback: StubProvider | None = None,
    cap: Decimal = CAP,
) -> LlmGateway:
    return LlmGateway(
        primary=primary,
        fallback=fallback,
        session_factory=factory,
        cap_usd=cap,
        clock=lambda: NOW,
    )


def _spent_in_march_2031(factory: sessionmaker[Session], cost_usd: str) -> None:
    with session_scope(factory) as writer:
        writer.add(
            LlmCall(
                provider=LlmProvider.PRIMARY,
                model_name="example-small-model",
                prompt_version="extraction-v1",
                purpose=PURPOSE,
                input_tokens=520,
                output_tokens=75,
                cost_usd=Decimal(cost_usd),
                created_at=datetime(2031, 3, 3, tzinfo=UTC),
            )
        )


def _new_calls(factory: sessionmaker[Session]) -> list[LlmCall]:
    """The rows a gateway call stored: the database dates them today, not in March 2031."""
    march_2031 = datetime(2031, 3, 1, tzinfo=UTC)
    with session_scope(factory) as reader:
        return list(
            reader.scalars(
                select(LlmCall).where(LlmCall.purpose == PURPOSE, LlmCall.created_at < march_2031)
            )
        )


def test_primary_answers_and_the_call_is_recorded(session_factory: sessionmaker[Session]) -> None:
    primary = StubProvider("example-small-model")
    fallback = StubProvider("example-open-model")

    result = _gateway(session_factory, primary, fallback).complete(_request())

    assert result == LlmResult(
        text=ANSWER_TEXT,
        provider=LlmProvider.PRIMARY,
        model_name="example-small-model",
        prompt_version="extraction-v1",
        input_tokens=520,
        output_tokens=75,
        cost_usd=Decimal("0.000123"),
        stop_reason="stop",
    )
    assert fallback.requests == []
    (stored,) = _new_calls(session_factory)
    assert stored.provider is LlmProvider.PRIMARY
    assert stored.model_name == "example-small-model"
    assert stored.prompt_version == "extraction-v1"
    assert stored.input_tokens == 520
    assert stored.output_tokens == 75
    assert stored.cost_usd == Decimal("0.000123")


def test_fallback_answers_when_the_primary_is_unavailable(
    session_factory: sessionmaker[Session],
) -> None:
    primary = StubProvider("example-small-model", error=ProviderUnavailableError("server error"))
    fallback = StubProvider("example-open-model", cost="0.000050")

    result = _gateway(session_factory, primary, fallback).complete(_request())

    assert result.provider is LlmProvider.FALLBACK
    assert result.model_name == "example-open-model"
    assert len(primary.requests) == 1
    (stored,) = _new_calls(session_factory)
    assert stored.provider is LlmProvider.FALLBACK
    assert stored.cost_usd == Decimal("0.000050")


def test_over_budget_skips_the_primary_and_uses_a_fallback_that_fits(
    session_factory: sessionmaker[Session],
) -> None:
    _spent_in_march_2031(session_factory, "3.00")
    primary = StubProvider("example-small-model")
    free_fallback = StubProvider("example-open-model", highest_cost="0", cost="0")

    result = _gateway(session_factory, primary, free_fallback).complete(_request())

    assert primary.requests == []
    assert result.provider is LlmProvider.FALLBACK
    assert result.cost_usd == Decimal("0")
    (stored,) = _new_calls(session_factory)
    assert stored.cost_usd == Decimal("0")


def test_over_budget_with_no_fallback_calls_nothing_and_records_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    _spent_in_march_2031(session_factory, "3.00")
    primary = StubProvider("example-small-model")

    with pytest.raises(LlmBudgetExceededError, match="no fallback is configured"):
        _gateway(session_factory, primary).complete(_request())

    assert primary.requests == []
    assert _new_calls(session_factory) == []


def test_over_budget_on_both_providers_calls_neither(
    session_factory: sessionmaker[Session],
) -> None:
    _spent_in_march_2031(session_factory, "3.00")
    primary = StubProvider("example-small-model")
    paid_fallback = StubProvider("example-open-model", highest_cost="0.005")

    with pytest.raises(LlmBudgetExceededError, match="primary or the fallback"):
        _gateway(session_factory, primary, paid_fallback).complete(_request())

    assert primary.requests == []
    assert paid_fallback.requests == []
    assert _new_calls(session_factory) == []


def test_a_failed_primary_does_not_let_the_fallback_overspend(
    session_factory: sessionmaker[Session],
) -> None:
    _spent_in_march_2031(session_factory, "2.99")
    primary = StubProvider(
        "example-small-model", highest_cost="0.01", error=ProviderUnavailableError("rate limit")
    )
    dearer_fallback = StubProvider("example-open-model", highest_cost="0.02")

    with pytest.raises(LlmBudgetExceededError):
        _gateway(session_factory, primary, dearer_fallback).complete(_request())

    assert dearer_fallback.requests == []
    assert _new_calls(session_factory) == []


def test_both_providers_unavailable_is_one_clear_error(
    session_factory: sessionmaker[Session],
) -> None:
    primary = StubProvider("example-small-model", error=ProviderUnavailableError("timeout"))
    fallback = StubProvider("example-open-model", error=ProviderUnavailableError("server error"))

    with pytest.raises(LlmUnavailableError, match="no provider could answer"):
        _gateway(session_factory, primary, fallback).complete(_request())

    assert len(primary.requests) == 1
    assert len(fallback.requests) == 1
    assert _new_calls(session_factory) == []


def test_primary_unavailable_with_no_fallback_is_an_error(
    session_factory: sessionmaker[Session],
) -> None:
    primary = StubProvider("example-small-model", error=ProviderUnavailableError("timeout"))

    with pytest.raises(LlmUnavailableError, match="no fallback is configured"):
        _gateway(session_factory, primary).complete(_request())

    assert _new_calls(session_factory) == []


def test_a_bad_request_is_raised_as_it_is_and_not_sent_to_the_fallback(
    session_factory: sessionmaker[Session],
) -> None:
    primary = StubProvider("example-small-model", error=RuntimeError("bad request"))
    fallback = StubProvider("example-open-model")

    with pytest.raises(RuntimeError, match="bad request"):
        _gateway(session_factory, primary, fallback).complete(_request())

    assert fallback.requests == []
    assert _new_calls(session_factory) == []


def test_a_call_that_lands_exactly_on_the_cap_is_made(
    session_factory: sessionmaker[Session],
) -> None:
    _spent_in_march_2031(session_factory, "2.99")
    primary = StubProvider("example-small-model", highest_cost="0.01")

    result = _gateway(session_factory, primary).complete(_request())

    assert result.provider is LlmProvider.PRIMARY


def test_a_call_one_millionth_of_a_dollar_over_the_cap_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    _spent_in_march_2031(session_factory, "2.99")
    primary = StubProvider("example-small-model", highest_cost="0.010001")

    with pytest.raises(LlmBudgetExceededError):
        _gateway(session_factory, primary).complete(_request())

    assert primary.requests == []


def test_a_recorded_call_counts_against_the_next_call(
    session_factory: sessionmaker[Session],
) -> None:
    # The real clock, because a recorded row is dated by the database.
    with session_scope(session_factory) as reader:
        spent = month_to_date_spend(reader, datetime.now(UTC))
    primary = StubProvider("example-small-model", highest_cost="0.01", cost="0.01")
    gateway = LlmGateway(
        primary=primary,
        fallback=None,
        session_factory=session_factory,
        cap_usd=spent + Decimal("0.01") + ONE_MILLIONTH,
    )

    gateway.complete(_request())
    with pytest.raises(LlmBudgetExceededError):
        gateway.complete(_request())

    assert len(primary.requests) == 1


def test_one_log_line_per_call_without_the_prompt_or_the_answer(
    session_factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    primary = StubProvider("example-small-model", error=ProviderUnavailableError("server error"))
    fallback = StubProvider("example-open-model", cost="0.000050")

    with caplog.at_level(logging.INFO, logger="src.llm.gateway"):
        _gateway(session_factory, primary, fallback).complete(_request())

    (call_line,) = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert call_line == (
        "llm call: provider=fallback model=example-open-model purpose=gateway-test "
        "input_tokens=520 output_tokens=75 cost_usd=0.000050 fallback_used=True"
    )
    assert PROMPT_TEXT not in caplog.text
    assert ANSWER_TEXT not in caplog.text


def test_an_answered_call_that_cannot_be_recorded_is_logged_and_the_error_raised(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_record(*_: object) -> None:
        raise RuntimeError("made-up database failure")

    monkeypatch.setattr(gateway_module, "record_llm_call", failing_record)
    primary = StubProvider("example-small-model")
    fallback = StubProvider("example-open-model")

    with (
        caplog.at_level(logging.INFO, logger="src.llm.gateway"),
        pytest.raises(RuntimeError, match="made-up database failure"),
    ):
        _gateway(session_factory, primary, fallback).complete(_request())

    (line,) = [r.getMessage() for r in caplog.records]
    assert line == (
        "llm call answered but not recorded: provider=primary model=example-small-model "
        "purpose=gateway-test input_tokens=520 output_tokens=75 cost_usd=0.000123"
    )
    assert caplog.records[0].levelno == logging.ERROR
    assert PROMPT_TEXT not in caplog.text
    assert ANSWER_TEXT not in caplog.text
    # A paid call is not paid for twice: the fallback is not tried.
    assert fallback.requests == []


def test_printing_a_request_an_answer_or_a_result_shows_no_prompt_and_no_answer(
    session_factory: sessionmaker[Session],
) -> None:
    request = _request()
    primary = StubProvider("example-small-model")
    result = _gateway(session_factory, primary).complete(request)
    answer = StubProvider("example-small-model").run(request)

    for printed in (repr(request), str(request), repr(answer), repr(result)):
        assert PROMPT_TEXT not in printed
        assert request.system not in printed
        assert ANSWER_TEXT not in printed
    assert "purpose='gateway-test'" in repr(request)


@pytest.mark.parametrize("cost", ["0.0000001", "1000000.000000", "-0.000001"])
def test_answer_rejects_a_cost_the_spend_table_cannot_hold(cost: str) -> None:
    with pytest.raises(ValidationError, match="cost_usd"):
        ProviderAnswer(
            text=ANSWER_TEXT,
            input_tokens=520,
            output_tokens=75,
            cost_usd=Decimal(cost),
            stop_reason="stop",
        )


@pytest.mark.parametrize("model_name", ["", "m" * 101])
def test_rejects_a_model_name_the_spend_table_cannot_hold(
    session_factory: sessionmaker[Session], model_name: str
) -> None:
    with pytest.raises(ValueError, match="1 to 100 characters"):
        _gateway(session_factory, StubProvider("example-small-model"), StubProvider(model_name))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("system", ""),
        ("user", ""),
        ("max_tokens", 0),
        ("prompt_version", ""),
        ("prompt_version", "p" * 41),
        ("purpose", ""),
        ("purpose", "p" * 41),
    ],
)
def test_request_rejects_a_value_the_gateway_cannot_use(field: str, value: Any) -> None:
    with pytest.raises(ValidationError, match=field):
        _request(**{field: value})
