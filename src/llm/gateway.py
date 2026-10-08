"""The LLM gateway: the one entry point every model call goes through.

`LlmGateway.complete` checks the monthly budget first, calls the primary provider, falls
back to a second provider when the primary is over budget or unavailable, and records what
the answered call cost. Over budget means fallback or refuse, never overspend.

The gateway knows providers only through the small `ChatProvider` interface, so the tests
use stubs and no real model is called. It returns text: turning the text into a typed
schema is the caller's job.

Not handled here: a call that fails after the provider already produced an answer (a
timeout while the answer travels back) may be billed but is not recorded, and two processes
checking the budget at the same moment. An answered call whose spend row cannot be stored is
logged as an error and the error is raised: the answer is not returned.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import LlmProvider
from src.db.session import session_scope
from src.llm.budget import MAX_TOKEN_COUNT, LlmCallRecord, check_budget, record_llm_call

logger = logging.getLogger(__name__)

# The lengths `llm_calls` can hold (see `LlmCallRecord`).
MAX_MODEL_NAME_LENGTH = 100
MAX_LABEL_LENGTH = 40


class LlmGatewayError(Exception):
    """Base class of the errors the gateway raises itself."""


class ProviderUnavailableError(LlmGatewayError):
    """Raised by a provider for a failure another provider may cover.

    That is a rate limit, a connection error, a timeout or a server error. A bad request or
    a wrong key is a bug or a setup mistake: the provider lets it propagate as it is.
    """


class LlmBudgetExceededError(LlmGatewayError):
    """The call does not fit this month's budget and no provider may answer it."""


class LlmUnavailableError(LlmGatewayError):
    """No provider could answer the call."""


class LlmRequest(BaseModel):
    """One model call: the prompt, the output limit, and the labels stored with the call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Left out of the repr: the prompt may hold document text, which must never reach a log.
    system: str = Field(min_length=1, repr=False)
    user: str = Field(min_length=1, repr=False)
    max_tokens: int = Field(gt=0, le=MAX_TOKEN_COUNT)
    prompt_version: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    purpose: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)


class ProviderAnswer(BaseModel):
    """What one provider returned for one request, with what the call cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(repr=False)
    input_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)
    output_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)
    # The same limits as `llm_calls.cost_usd`, so a cost the table cannot hold fails here.
    cost_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=6)
    stop_reason: str


class LlmResult(BaseModel):
    """The gateway's answer: the text, who produced it, and what it cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(repr=False)
    provider: LlmProvider
    model_name: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    stop_reason: str


class ChatProvider(Protocol):
    """A service that can answer a request, and say beforehand what it costs at most."""

    @property
    def model_name(self) -> str:
        """The model this provider calls."""
        ...

    def highest_cost(self, request: LlmRequest) -> Decimal:
        """Return an amount in US dollars that a call for `request` can never exceed."""
        ...

    def run(self, request: LlmRequest) -> ProviderAnswer:
        """Answer `request`. Spends money and can take several seconds.

        Raises `ProviderUnavailableError` for a failure another provider may cover.
        """
        ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LlmGateway:
    """The one entry point for model calls: budget check first, spend recorded after."""

    def __init__(
        self,
        *,
        primary: ChatProvider,
        fallback: ChatProvider | None,
        session_factory: sessionmaker[Session],
        cap_usd: Decimal,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        # Checked here, not after a paid call, when the spend row could no longer be stored.
        for provider in (primary, fallback):
            if provider is not None and not 0 < len(provider.model_name) <= MAX_MODEL_NAME_LENGTH:
                raise ValueError(f"a model name must have 1 to {MAX_MODEL_NAME_LENGTH} characters")
        self._primary = primary
        self._fallback = fallback
        self._session_factory = session_factory
        self._cap_usd = cap_usd
        self._clock = clock

    def complete(self, request: LlmRequest) -> LlmResult:
        """Answer `request` with the primary provider, or the fallback, within the budget.

        Spends money and can take several seconds. Raises `LlmBudgetExceededError` when no
        provider fits the monthly budget (nothing is called) and `LlmUnavailableError` when
        no provider could answer.
        """
        primary_error: ProviderUnavailableError | None = None
        if self._fits_budget(self._primary, request):
            try:
                return self._answer(LlmProvider.PRIMARY, self._primary, request)
            except ProviderUnavailableError as error:
                primary_error = error
                # The reason only: an error's text may quote the request.
                logger.warning("llm primary provider unavailable: %s", error)

        if self._fallback is None:
            if primary_error is None:
                raise LlmBudgetExceededError(
                    "the call does not fit the monthly LLM budget and no fallback is configured"
                )
            raise LlmUnavailableError(
                "the primary provider is unavailable and no fallback is configured"
            ) from primary_error
        if not self._fits_budget(self._fallback, request):
            raise LlmBudgetExceededError(
                "the call does not fit the monthly LLM budget on the primary or the fallback"
            ) from primary_error
        try:
            return self._answer(LlmProvider.FALLBACK, self._fallback, request)
        except ProviderUnavailableError as error:
            raise LlmUnavailableError("no provider could answer the call") from error

    def _fits_budget(self, provider: ChatProvider, request: LlmRequest) -> bool:
        with session_scope(self._session_factory) as session:
            decision = check_budget(
                session,
                cap_usd=self._cap_usd,
                highest_cost_usd=provider.highest_cost(request),
                now=self._clock(),
            )
        return decision.allowed

    def _answer(self, role: LlmProvider, provider: ChatProvider, request: LlmRequest) -> LlmResult:
        answer = provider.run(request)
        try:
            record_llm_call(
                self._session_factory,
                LlmCallRecord(
                    provider=role,
                    model_name=provider.model_name,
                    prompt_version=request.prompt_version,
                    purpose=request.purpose,
                    input_tokens=answer.input_tokens,
                    output_tokens=answer.output_tokens,
                    cost_usd=answer.cost_usd,
                ),
            )
        except Exception:
            # The call was paid for but its spend is not stored: say so before the error leaves.
            logger.error(
                "llm call answered but not recorded: provider=%s model=%s purpose=%s "
                "input_tokens=%d output_tokens=%d cost_usd=%s",
                role.value,
                provider.model_name,
                request.purpose,
                answer.input_tokens,
                answer.output_tokens,
                answer.cost_usd,
            )
            raise
        logger.info(
            "llm call: provider=%s model=%s purpose=%s input_tokens=%d output_tokens=%d "
            "cost_usd=%s fallback_used=%s",
            role.value,
            provider.model_name,
            request.purpose,
            answer.input_tokens,
            answer.output_tokens,
            answer.cost_usd,
            role is LlmProvider.FALLBACK,
        )
        return LlmResult(
            text=answer.text,
            provider=role,
            model_name=provider.model_name,
            prompt_version=request.prompt_version,
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
            cost_usd=answer.cost_usd,
            stop_reason=answer.stop_reason,
        )
