"""A gateway provider for any service that speaks the OpenAI chat format.

The primary and the fallback provider are both this class: they differ only in base URL,
model, key and prices, which all come from `LlmGatewaySettings`.
"""

import json
from decimal import Decimal

from openai import (
    APIConnectionError,
    APIStatusError,
    InternalServerError,
    Omit,
    OpenAI,
    RateLimitError,
    omit,
)
from openai.types.shared_params import ResponseFormatJSONSchema
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import LlmGatewaySettings
from src.llm.budget import call_cost
from src.llm.gateway import (
    LlmGateway,
    LlmRequest,
    ProviderAnswer,
    ProviderRejectedError,
    ProviderUnavailableError,
)

# Added to the byte count of the prompt for what the service wraps around the messages
# (role markers, a model's built-in preamble). Chosen with a wide margin, not measured.
MESSAGE_FRAMING_TOKENS = 128
# The SDK refuses to build a client without a key; a host that needs none ignores this one.
NO_API_KEY = "not-set"
# Stored as the stop reason when the service returned no choice at all.
NO_CHOICE_STOP_REASON = "no_choice"
# The gateway's fallback is the retry: a retry inside the SDK would be a second paid request
# under one budget check and one spend row.
SDK_MAX_RETRIES = 0
# Status codes the service uses for a passing failure that has no error class of its own:
# request timeout and conflict.
TEMPORARY_STATUS_CODES = frozenset({408, 409})
# The name the service wants for a response schema. It is a label only.
RESPONSE_SCHEMA_NAME = "answer"
# The service aims for the schema but does not promise it. Strict mode is off because the
# fallback service refused these schemas in strict mode (measured 2026-10-09).
RESPONSE_SCHEMA_STRICT = False


def highest_input_tokens(request: LlmRequest) -> int:
    """Return a number of input tokens that `request` can never exceed.

    There is no free token counter, so this is the UTF-8 byte length of the prompt (a token
    is never smaller than one byte) plus a fixed margin for message framing. A response
    schema is billed as input too, so its bytes are counted as well.
    """
    prompt_bytes = len(request.system.encode("utf-8")) + len(request.user.encode("utf-8"))
    schema_bytes = 0
    if request.response_schema is not None:
        schema_bytes = len(json.dumps(request.response_schema).encode("utf-8"))
    return prompt_bytes + schema_bytes + MESSAGE_FRAMING_TOKENS


def _response_format(request: LlmRequest) -> ResponseFormatJSONSchema | Omit:
    if request.response_schema is None:
        return omit
    return {
        "type": "json_schema",
        "json_schema": {
            "name": RESPONSE_SCHEMA_NAME,
            "schema": request.response_schema,
            "strict": RESPONSE_SCHEMA_STRICT,
        },
    }


class OpenAiChatProvider:
    """Calls one model of one OpenAI-compatible service and prices the call."""

    def __init__(
        self,
        *,
        client: OpenAI,
        model_name: str,
        input_usd_per_mtok: Decimal,
        output_usd_per_mtok: Decimal,
    ) -> None:
        self._client = client
        self._model_name = model_name
        self._input_usd_per_mtok = input_usd_per_mtok
        self._output_usd_per_mtok = output_usd_per_mtok

    @property
    def model_name(self) -> str:
        """The model this provider calls."""
        return self._model_name

    def highest_cost(self, request: LlmRequest) -> Decimal:
        """Return the cost of `request` at its highest possible token counts. No request is made."""
        return self._cost(highest_input_tokens(request), request.max_tokens)

    def run(self, request: LlmRequest) -> ProviderAnswer:
        """Send `request` to the service. Spends money and can take several seconds.

        A rate limit, a connection error, a timeout, a server error or a status in
        `TEMPORARY_STATUS_CODES` is raised as `ProviderUnavailableError`; any other status
        the service answers with (a bad request, a wrong key) as `ProviderRejectedError`.
        """
        try:
            completion = self._client.chat.completions.create(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                max_completion_tokens=request.max_tokens,
                response_format=_response_format(request),
            )
        except (APIConnectionError, RateLimitError, InternalServerError) as error:
            # The class name only: the SDK's message may quote the request.
            raise ProviderUnavailableError(type(error).__name__) from error
        except APIStatusError as error:
            if error.status_code in TEMPORARY_STATUS_CODES:
                raise ProviderUnavailableError(type(error).__name__) from error
            # Not chained: the SDK's message holds the service's whole response body, which
            # may quote the request or a failed answer, and a traceback would print it.
            raise ProviderRejectedError(
                f"{type(error).__name__} (status {error.status_code})"
            ) from None

        if completion.usage is None:
            # The call was answered, so it was paid for: record the most it can have cost.
            input_tokens = highest_input_tokens(request)
            output_tokens = request.max_tokens
        else:
            input_tokens = completion.usage.prompt_tokens
            output_tokens = completion.usage.completion_tokens
        if completion.choices:
            text = completion.choices[0].message.content or ""
            stop_reason: str = completion.choices[0].finish_reason
        else:
            text = ""
            stop_reason = NO_CHOICE_STOP_REASON
        return ProviderAnswer(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self._cost(input_tokens, output_tokens),
            stop_reason=stop_reason,
        )

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return call_cost(
            input_tokens, output_tokens, self._input_usd_per_mtok, self._output_usd_per_mtok
        )


def build_openai_providers(
    settings: LlmGatewaySettings,
) -> tuple[OpenAiChatProvider, OpenAiChatProvider | None]:
    """Build the primary provider, and the fallback when one is configured. No request is made."""
    primary = OpenAiChatProvider(
        client=OpenAI(
            api_key=settings.llm_primary_api_key.get_secret_value(),
            base_url=settings.llm_primary_base_url,
            max_retries=SDK_MAX_RETRIES,
            timeout=settings.llm_timeout_seconds,
        ),
        model_name=settings.llm_primary_model,
        input_usd_per_mtok=settings.llm_primary_input_usd_per_mtok,
        output_usd_per_mtok=settings.llm_primary_output_usd_per_mtok,
    )
    if settings.llm_fallback_base_url is None or settings.llm_fallback_model is None:
        return primary, None
    fallback_key = settings.llm_fallback_api_key
    fallback = OpenAiChatProvider(
        client=OpenAI(
            api_key=NO_API_KEY if fallback_key is None else fallback_key.get_secret_value(),
            base_url=settings.llm_fallback_base_url,
            max_retries=SDK_MAX_RETRIES,
            timeout=settings.llm_timeout_seconds,
        ),
        model_name=settings.llm_fallback_model,
        input_usd_per_mtok=settings.llm_fallback_input_usd_per_mtok,
        output_usd_per_mtok=settings.llm_fallback_output_usd_per_mtok,
    )
    return primary, fallback


def build_openai_gateway(
    settings: LlmGatewaySettings, session_factory: sessionmaker[Session]
) -> LlmGateway:
    """Build the gateway from the settings, with the monthly budget as its cap."""
    primary, fallback = build_openai_providers(settings)
    return LlmGateway(
        primary=primary,
        fallback=fallback,
        session_factory=session_factory,
        cap_usd=settings.llm_monthly_budget_usd,
    )
