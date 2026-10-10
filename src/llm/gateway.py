"""The LLM gateway: the one entry point every model call goes through.

`LlmGateway.complete` checks the monthly budget first, calls the primary provider, falls
back to a second provider when the primary is over budget or unavailable, and records what
the answered call cost. Over budget means fallback or refuse, never overspend.

`LlmGateway.embed` turns a batch of texts into vectors under the same budget check and the
same spend table. It has no fallback: vectors from two models cannot be compared.

The gateway knows providers only through the small `ChatProvider` and `EmbeddingProvider`
interfaces, so the tests use stubs and no real model is called. `complete` returns text:
turning the text into a typed schema is the caller's job.

Not handled here: a call that fails after the provider already produced an answer (a
timeout while the answer travels back) may be billed but is not recorded, and two processes
checking the budget at the same moment. An answered call whose spend row cannot be stored is
logged as an error and the error is raised: the answer is not returned.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Protocol

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

    That is a rate limit, a connection error, a timeout or a server error.
    """


class ProviderRejectedError(LlmGatewayError):
    """Raised by a provider when the service refused the request itself.

    A bad request or a wrong key is a bug or a setup mistake. Another provider would not fix
    it, so the gateway does not try the fallback and the error leaves `complete` as it is.
    Its text holds the kind of failure and the status only, never what the service answered.
    """


class LlmBudgetExceededError(LlmGatewayError):
    """The call does not fit this month's budget and no provider may answer it."""


class LlmUnavailableError(LlmGatewayError):
    """No provider could answer the call."""


class EmbeddingNotConfiguredError(LlmGatewayError):
    """`embed` was called on a gateway that was built without an embedding provider."""


class EmbeddingAnswerError(LlmGatewayError):
    """The provider answered with another number of vectors than texts were sent.

    The call was paid for and its spend is recorded before this is raised.
    """


class LlmRequest(BaseModel):
    """One model call: the prompt, the output limit, and the labels stored with the call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Left out of the repr: the prompt may hold document text, which must never reach a log.
    system: str = Field(min_length=1, repr=False)
    user: str = Field(min_length=1, repr=False)
    max_tokens: int = Field(gt=0, le=MAX_TOKEN_COUNT)
    prompt_version: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    purpose: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    # A JSON schema the answer must fit, sent to the provider with the prompt. The provider
    # only shapes the answer with it: checking the answer stays the caller's job.
    response_schema: dict[str, Any] | None = None


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


class EmbeddingRequest(BaseModel):
    """One embedding call: a batch of texts, and the labels stored with the call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Left out of the repr, like a prompt: a text must never reach a log.
    texts: tuple[Annotated[str, Field(min_length=1)], ...] = Field(min_length=1, repr=False)
    # Names how the embedded text was built. Stored in `llm_calls.prompt_version`.
    input_version: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    purpose: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)


class EmbeddingAnswer(BaseModel):
    """What the embedding provider returned for one request, with what the call cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # One vector per text, in the order of the request's texts.
    vectors: tuple[tuple[float, ...], ...] = Field(repr=False)
    input_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)
    # The same limits as `llm_calls.cost_usd`, so a cost the table cannot hold fails here.
    cost_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=6)


class EmbeddingResult(BaseModel):
    """The gateway's answer to `embed`: the vectors, the model that made them, and the cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # One vector per text, in the order of the request's texts.
    vectors: tuple[tuple[float, ...], ...] = Field(repr=False)
    model_name: str
    input_version: str
    input_tokens: int
    cost_usd: Decimal


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

        Raises `ProviderUnavailableError` for a failure another provider may cover and
        `ProviderRejectedError` when the service refused the request.
        """
        ...


class EmbeddingProvider(Protocol):
    """A service that can embed a batch of texts, and say beforehand what it costs at most."""

    @property
    def model_name(self) -> str:
        """The model this provider calls."""
        ...

    def highest_cost(self, request: EmbeddingRequest) -> Decimal:
        """Return an amount in US dollars that a call for `request` can never exceed."""
        ...

    def run(self, request: EmbeddingRequest) -> EmbeddingAnswer:
        """Embed the texts of `request`. Spends money and can take several seconds.

        Raises `ProviderUnavailableError` for a passing failure and `ProviderRejectedError`
        when the service refused the request.
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
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        # Checked here, not after a paid call, when the spend row could no longer be stored.
        for provider in (primary, fallback, embedder):
            if provider is not None and not 0 < len(provider.model_name) <= MAX_MODEL_NAME_LENGTH:
                raise ValueError(f"a model name must have 1 to {MAX_MODEL_NAME_LENGTH} characters")
        self._primary = primary
        self._fallback = fallback
        self._embedder = embedder
        self._session_factory = session_factory
        self._cap_usd = cap_usd
        self._clock = clock

    def complete(self, request: LlmRequest) -> LlmResult:
        """Answer `request` with the primary provider, or the fallback, within the budget.

        Spends money and can take several seconds. Raises `LlmBudgetExceededError` when no
        provider fits the monthly budget (nothing is called) and `LlmUnavailableError` when
        no provider could answer. A provider's `ProviderRejectedError` is not covered by the
        fallback and is raised as it is.
        """
        primary_error: ProviderUnavailableError | None = None
        if self._fits_budget(self._primary.highest_cost(request)):
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
        if not self._fits_budget(self._fallback.highest_cost(request)):
            raise LlmBudgetExceededError(
                "the call does not fit the monthly LLM budget on the primary or the fallback"
            ) from primary_error
        try:
            return self._answer(LlmProvider.FALLBACK, self._fallback, request)
        except ProviderUnavailableError as error:
            raise LlmUnavailableError("no provider could answer the call") from error

    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Turn the texts of `request` into vectors, one per text, within the budget.

        Spends money and can take several seconds. There is no fallback: vectors from two
        models cannot be compared. Raises `LlmBudgetExceededError` when the call does not fit
        the monthly budget (nothing is called), `LlmUnavailableError` when the provider could
        not answer, and `EmbeddingAnswerError` when the number of vectors is not the number of
        texts (the spend is recorded first). A provider's `ProviderRejectedError` is raised as
        it is.
        """
        embedder = self._embedder
        if embedder is None:
            raise EmbeddingNotConfiguredError("this gateway has no embedding provider")
        if not self._fits_budget(embedder.highest_cost(request)):
            raise LlmBudgetExceededError("the embedding call does not fit the monthly LLM budget")
        try:
            answer = embedder.run(request)
        except ProviderUnavailableError as error:
            raise LlmUnavailableError("the embedding provider is unavailable") from error
        try:
            record_llm_call(
                self._session_factory,
                LlmCallRecord(
                    provider=LlmProvider.PRIMARY,
                    model_name=embedder.model_name,
                    prompt_version=request.input_version,
                    purpose=request.purpose,
                    input_tokens=answer.input_tokens,
                    output_tokens=0,
                    cost_usd=answer.cost_usd,
                ),
            )
        except Exception:
            # The call was paid for but its spend is not stored: say so before the error leaves.
            logger.error(
                "llm embedding answered but not recorded: model=%s purpose=%s texts=%d "
                "input_tokens=%d cost_usd=%s",
                embedder.model_name,
                request.purpose,
                len(request.texts),
                answer.input_tokens,
                answer.cost_usd,
            )
            raise
        logger.info(
            "llm embedding: model=%s purpose=%s texts=%d input_tokens=%d cost_usd=%s",
            embedder.model_name,
            request.purpose,
            len(request.texts),
            answer.input_tokens,
            answer.cost_usd,
        )
        if len(answer.vectors) != len(request.texts):
            raise EmbeddingAnswerError(
                f"the provider returned {len(answer.vectors)} vectors for "
                f"{len(request.texts)} texts"
            )
        return EmbeddingResult(
            vectors=answer.vectors,
            model_name=embedder.model_name,
            input_version=request.input_version,
            input_tokens=answer.input_tokens,
            cost_usd=answer.cost_usd,
        )

    def _fits_budget(self, highest_cost_usd: Decimal) -> bool:
        with session_scope(self._session_factory) as session:
            decision = check_budget(
                session,
                cap_usd=self._cap_usd,
                highest_cost_usd=highest_cost_usd,
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
