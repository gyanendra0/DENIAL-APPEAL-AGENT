import json
import traceback
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import openai
import pytest
from openai import OpenAI
from openai.types.chat import ChatCompletion
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import LlmGatewaySettings
from src.llm import openai_provider
from src.llm.gateway import (
    LlmGateway,
    LlmRequest,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from src.llm.openai_provider import (
    MESSAGE_FRAMING_TOKENS,
    NO_API_KEY,
    OpenAiChatProvider,
    build_openai_gateway,
    build_openai_providers,
    highest_input_tokens,
)

MADE_UP_KEY = "made-up-key-for-tests"
# httpx2 comes with the SDK; it is used here only to build the SDK's own error objects.
HTTP_REQUEST = httpx2.Request("POST", "https://llm.example.test/v1/chat/completions")
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"reason": {"type": "string"}},
    "required": ["reason"],
    "additionalProperties": False,
}


class FakeCompletions:
    """Stands in for the SDK's `chat.completions`: no network, it remembers its arguments."""

    def __init__(self, outcome: ChatCompletion | Exception) -> None:
        self._outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _provider(outcome: ChatCompletion | Exception) -> tuple[OpenAiChatProvider, FakeCompletions]:
    completions = FakeCompletions(outcome)
    client = cast(OpenAI, SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    provider = OpenAiChatProvider(
        client=client,
        model_name="example-small-model",
        input_usd_per_mtok=Decimal("0.15"),
        output_usd_per_mtok=Decimal("0.60"),
    )
    return provider, completions


def _completion(**overrides: Any) -> ChatCompletion:
    fields: dict[str, Any] = {
        "id": "made-up-id",
        "object": "chat.completion",
        "created": 0,
        "model": "example-small-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "made-up answer"},
            }
        ],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
    }
    return ChatCompletion.model_validate(fields | overrides)


def _request(**overrides: Any) -> LlmRequest:
    fields: dict[str, Any] = {
        "system": "abcd",
        "user": "efghij",
        "max_tokens": 200,
        "prompt_version": "extraction-v1",
        "purpose": "extraction",
    }
    return LlmRequest(**(fields | overrides))


def _status_error(error_class: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    response = httpx2.Response(status, request=HTTP_REQUEST)
    return error_class("made-up failure", response=response, body=None)


def test_highest_input_tokens_is_the_prompts_byte_length_plus_the_framing_margin() -> None:
    assert highest_input_tokens(_request()) == 4 + 6 + MESSAGE_FRAMING_TOKENS


def test_highest_input_tokens_counts_bytes_not_characters() -> None:
    # "é" is one character and two bytes; a tokenizer may spend two tokens on it.
    assert highest_input_tokens(_request(user="é")) == 4 + 2 + MESSAGE_FRAMING_TOKENS


def test_highest_cost_prices_the_byte_bound_and_the_whole_output_limit() -> None:
    provider, completions = _provider(_completion())

    cost = provider.highest_cost(_request())

    # (10 + 128) x 0.15 / 1,000,000 + 200 x 0.60 / 1,000,000 = 0.0001407, rounded up.
    assert cost == Decimal("0.000141")
    assert completions.calls == []


def test_run_sends_the_model_the_two_messages_and_the_output_limit() -> None:
    provider, completions = _provider(_completion())

    provider.run(_request())

    assert completions.calls == [
        {
            "model": "example-small-model",
            "messages": [
                {"role": "system", "content": "abcd"},
                {"role": "user", "content": "efghij"},
            ],
            "max_completion_tokens": 200,
            "response_format": openai.omit,
        }
    ]


def test_run_sends_a_response_schema_as_the_response_format() -> None:
    provider, completions = _provider(_completion())

    provider.run(_request(response_schema=RESPONSE_SCHEMA))

    assert completions.calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": RESPONSE_SCHEMA, "strict": False},
    }


def test_highest_input_tokens_counts_the_response_schema_too() -> None:
    schema_bytes = len(json.dumps(RESPONSE_SCHEMA).encode("utf-8"))

    bound = highest_input_tokens(_request(response_schema=RESPONSE_SCHEMA))

    assert schema_bytes > 50
    assert bound == 4 + 6 + schema_bytes + MESSAGE_FRAMING_TOKENS


def test_run_returns_the_text_and_prices_the_reported_tokens() -> None:
    provider, _ = _provider(_completion())

    answer = provider.run(_request())

    assert answer.text == "made-up answer"
    assert answer.stop_reason == "stop"
    assert answer.input_tokens == 1000
    assert answer.output_tokens == 200
    # 1000 x 0.15 / 1,000,000 + 200 x 0.60 / 1,000,000
    assert answer.cost_usd == Decimal("0.000270")


def test_run_records_the_highest_possible_counts_when_none_are_reported() -> None:
    provider, _ = _provider(_completion(usage=None))

    answer = provider.run(_request())

    assert answer.input_tokens == 10 + MESSAGE_FRAMING_TOKENS
    assert answer.output_tokens == 200
    # The same amount as the highest possible cost: (10 + 128) x 0.15 + 200 x 0.60, per million.
    assert answer.cost_usd == Decimal("0.000141")


def test_run_returns_empty_text_when_the_answer_has_no_content() -> None:
    choice = {
        "index": 0,
        "finish_reason": "length",
        "message": {"role": "assistant", "content": None},
    }
    provider, _ = _provider(_completion(choices=[choice]))

    answer = provider.run(_request())

    assert answer.text == ""
    assert answer.stop_reason == "length"


def test_run_still_prices_an_answer_with_no_choice() -> None:
    provider, _ = _provider(_completion(choices=[]))

    answer = provider.run(_request())

    assert answer.text == ""
    assert answer.stop_reason == "no_choice"
    assert answer.cost_usd == Decimal("0.000270")


@pytest.mark.parametrize(
    "error",
    [
        _status_error(openai.RateLimitError, 429),
        _status_error(openai.InternalServerError, 503),
        openai.APIConnectionError(request=HTTP_REQUEST),
        openai.APITimeoutError(request=HTTP_REQUEST),
        _status_error(openai.APIStatusError, 408),
        _status_error(openai.ConflictError, 409),
    ],
    ids=[
        "rate limit",
        "server error",
        "connection error",
        "timeout",
        "request timeout status",
        "conflict status",
    ],
)
def test_run_reports_a_failure_the_fallback_may_cover(error: Exception) -> None:
    provider, _ = _provider(error)

    with pytest.raises(ProviderUnavailableError, match=type(error).__name__) as raised:
        provider.run(_request())

    assert raised.value.__cause__ is error
    assert "made-up failure" not in str(raised.value)


@pytest.mark.parametrize(
    ("error_class", "status"),
    [
        (openai.BadRequestError, 400),
        (openai.AuthenticationError, 401),
        (openai.PermissionDeniedError, 403),
        (openai.NotFoundError, 404),
        (openai.UnprocessableEntityError, 422),
        (openai.APIStatusError, 418),
    ],
)
def test_run_reports_a_refused_request_without_what_the_service_answered(
    error_class: type[openai.APIStatusError], status: int
) -> None:
    # The SDK puts the whole response body into its message; a body may quote an answer.
    body = {"error": {"message": "failed to fit the schema", "failed_generation": "Ada Example"}}
    response = httpx2.Response(status, request=HTTP_REQUEST, json=body)
    error = error_class(f"Error code: {status} - {body}", response=response, body=body)
    provider, _ = _provider(error)

    with pytest.raises(ProviderRejectedError) as raised:
        provider.run(_request())

    assert str(raised.value) == f"{error_class.__name__} (status {status})"
    printed = "".join(traceback.format_exception(raised.value))
    assert "Ada Example" not in printed
    assert "failed_generation" not in printed


def _settings(**overrides: Any) -> LlmGatewaySettings:
    fields: dict[str, Any] = {
        "llm_monthly_budget_usd": "3.00",
        "llm_timeout_seconds": "30",
        "llm_primary_base_url": "https://primary.example.test/v1",
        "llm_primary_model": "example-small-model",
        "llm_primary_api_key": MADE_UP_KEY,
        "llm_primary_input_usd_per_mtok": "0.15",
        "llm_primary_output_usd_per_mtok": "0.60",
    }
    return LlmGatewaySettings(_env_file=None, **(fields | overrides))  # type: ignore[call-arg]


@pytest.fixture
def client_arguments(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the SDK client class, so the arguments it is built with can be read."""
    seen: list[dict[str, Any]] = []

    def fake_client(**kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(openai_provider, "OpenAI", fake_client)
    return seen


def test_builds_only_the_primary_when_no_fallback_is_configured(
    client_arguments: list[dict[str, Any]],
) -> None:
    primary, fallback = build_openai_providers(_settings())

    assert fallback is None
    assert primary.model_name == "example-small-model"
    # No retry inside the SDK: each retry would be a paid request with no budget check.
    assert client_arguments == [
        {
            "api_key": MADE_UP_KEY,
            "base_url": "https://primary.example.test/v1",
            "max_retries": 0,
            "timeout": 30.0,
        }
    ]


def test_builds_the_fallback_with_its_own_host_key_and_prices(
    client_arguments: list[dict[str, Any]],
) -> None:
    _, fallback = build_openai_providers(
        _settings(
            llm_fallback_base_url="https://fallback.example.test/v1",
            llm_fallback_model="example-open-model",
            llm_fallback_api_key="another-made-up-key",
            llm_fallback_input_usd_per_mtok="1.00",
            llm_fallback_output_usd_per_mtok="2.00",
        )
    )

    assert fallback is not None
    assert fallback.model_name == "example-open-model"
    assert client_arguments[1] == {
        "api_key": "another-made-up-key",
        "base_url": "https://fallback.example.test/v1",
        "max_retries": 0,
        # The same limit as the primary: one setting covers both.
        "timeout": 30.0,
    }
    # (10 + 128) x 1.00 / 1,000,000 + 200 x 2.00 / 1,000,000
    assert fallback.highest_cost(_request()) == Decimal("0.000538")


def test_a_fallback_without_a_key_gets_the_placeholder_and_costs_nothing(
    client_arguments: list[dict[str, Any]],
) -> None:
    _, fallback = build_openai_providers(
        _settings(
            llm_fallback_base_url="http://localhost:11434/v1",
            llm_fallback_model="example-open-model",
        )
    )

    assert fallback is not None
    assert client_arguments[1]["api_key"] == NO_API_KEY
    assert fallback.highest_cost(_request()) == Decimal("0")


def test_builds_a_gateway_from_the_settings(client_arguments: list[dict[str, Any]]) -> None:
    gateway = build_openai_gateway(_settings(), sessionmaker[Session]())

    assert isinstance(gateway, LlmGateway)
    assert len(client_arguments) == 1
