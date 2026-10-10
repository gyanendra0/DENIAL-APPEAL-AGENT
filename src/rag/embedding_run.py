"""Embeds the stored evidence chunks, a batch at a time, and saves each batch as it arrives.

A chunk that already has a vector for the model and the input version is skipped: a run that
stopped half way is continued, not paid for twice. A new load of the evidence corpus deletes
the chunks and with them the vectors, so this run is needed again afterwards.

Only one run works at a time. Two runs at once would both see the same chunks as not embedded
and both pay for them, so a run first takes a lock in the database and a second run stops
before it calls the provider.
"""

import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    EMBEDDING_DIMENSIONS,
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
)
from src.db.session import session_scope
from src.llm.gateway import (
    EmbeddingAnswerError,
    EmbeddingRequest,
    EmbeddingResult,
    LlmBudgetExceededError,
    LlmGateway,
    LlmUnavailableError,
    ProviderRejectedError,
)
from src.rag.embedding_input import EMBEDDING_INPUT_VERSION, build_embedding_input

# The number of texts sent in one request. One request writes one spend row.
EMBEDDING_BATCH_SIZE = 100
# Stored in `llm_calls.purpose` with every call of this run.
EVIDENCE_EMBEDDING_PURPOSE = "evidence_embedding"
BUDGET_REACHED = "the monthly LLM budget is reached"
NO_PROVIDER = "the embedding provider could not answer"
REQUEST_REFUSED = "the provider refused the request"
ANSWER_NOT_USABLE = "the answer cannot be stored (the call was paid for)"
CHUNKS_CHANGED = "the evidence chunks changed during the run (the call was paid for)"
RUN_IN_PROGRESS = "another embedding run is in progress"
# The number of the advisory lock a run holds in the database while it works. Any fixed number
# that nothing else in the project uses.
EMBEDDING_RUN_LOCK_KEY = 4_111_001


class EmbeddingRunSummary(BaseModel):
    """What one run did with the stored chunks.

    `stop_reason` is None when every chunk without a vector was embedded; otherwise it says
    why the run ended early, and `not_tried` chunks are left for the next run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunks: int = Field(ge=0)
    already_embedded: int = Field(ge=0)
    embedded: int = Field(ge=0)
    stop_reason: str | None

    @property
    def not_tried(self) -> int:
        return self.chunks - self.already_embedded - self.embedded


def run_evidence_embedding(
    session_factory: sessionmaker[Session],
    gateway: LlmGateway,
    *,
    model_name: str,
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> EmbeddingRunSummary:
    """Embed every stored chunk that has no vector for `model_name`, and store the vectors.

    Spends money and can take a minute: one embedding call per `batch_size` chunks.
    `model_name` must be the model the gateway embeds with. Each batch is committed on its
    own, so a run that stops keeps what it finished. The run ends early, without an error,
    when the budget is reached, the provider cannot answer or refuses the request, or an
    answer cannot be stored: another model than `model_name`, another number of vectors than
    texts, or a vector that is not `EMBEDDING_DIMENSIONS` finite numbers. It ends early in the
    same way when a chunk of the batch is gone at the write, as after a load of the corpus
    during the run; the counts are then read again, because such a load also removes the
    vectors of the batches before. Nothing of such a batch is stored. It also ends early,
    before any call, when another run is in progress.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be 1 or more")
    with _single_run_lock(session_factory) as acquired:
        if not acquired:
            with session_scope(session_factory) as session:
                chunks = _count_chunks(session)
                waiting = len(_pending_inputs(session, model_name))
            return EmbeddingRunSummary(
                chunks=chunks,
                already_embedded=chunks - waiting,
                embedded=0,
                stop_reason=RUN_IN_PROGRESS,
            )
        return _embed_pending(session_factory, gateway, model_name, batch_size)


@contextmanager
def _single_run_lock(session_factory: sessionmaker[Session]) -> Iterator[bool]:
    """Take the run's advisory lock without waiting; yield whether this run got it.

    The lock belongs to one database connection, so the session is kept open, and its
    connection with it, until the run ends. The database drops the lock by itself when the
    connection closes, so a run that is killed does not leave it behind.
    """
    with session_factory() as session:
        acquired = bool(session.scalar(select(func.pg_try_advisory_lock(EMBEDDING_RUN_LOCK_KEY))))
        try:
            yield acquired
        finally:
            if acquired:
                session.scalar(select(func.pg_advisory_unlock(EMBEDDING_RUN_LOCK_KEY)))


def _count_chunks(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(EvidenceChunk)) or 0


def _embed_pending(
    session_factory: sessionmaker[Session], gateway: LlmGateway, model_name: str, batch_size: int
) -> EmbeddingRunSummary:
    # Read after the lock is taken, so the list cannot hold a chunk another run just embedded.
    with session_scope(session_factory) as session:
        chunks = _count_chunks(session)
        pending = _pending_inputs(session, model_name)

    embedded = 0
    stop_reason: str | None = None
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        request = EmbeddingRequest(
            texts=tuple(text for _, text in batch),
            input_version=EMBEDDING_INPUT_VERSION,
            purpose=EVIDENCE_EMBEDDING_PURPOSE,
        )
        try:
            result = gateway.embed(request)
        except LlmBudgetExceededError:
            stop_reason = BUDGET_REACHED
            break
        except LlmUnavailableError:
            stop_reason = NO_PROVIDER
            break
        except ProviderRejectedError as error:
            # The error's text is the kind of failure and the status only.
            stop_reason = f"{REQUEST_REFUSED}: {error}"
            break
        except EmbeddingAnswerError as error:
            stop_reason = f"{ANSWER_NOT_USABLE}: {error}"
            break
        problem = _answer_problem(result, model_name)
        if problem is not None:
            stop_reason = f"{ANSWER_NOT_USABLE}: {problem}"
            break
        try:
            with session_scope(session_factory) as session:
                session.add_all(
                    EvidenceChunkEmbedding(
                        evidence_chunk_id=chunk_id,
                        model_name=model_name,
                        input_version=EMBEDDING_INPUT_VERSION,
                        embedding=list(vector),
                    )
                    for (chunk_id, _), vector in zip(batch, result.vectors, strict=True)
                )
        except IntegrityError:
            # The lock stops a second run, not the corpus loader: a load during the run removes
            # the chunks read above. The error's text would hold the vectors, so it is dropped.
            stop_reason = CHUNKS_CHANGED
            break
        embedded += len(batch)
    already_embedded = chunks - len(pending)
    if stop_reason == CHUNKS_CHANGED:
        # The counts read before the loop are out of date, and a load of the corpus removed
        # the vectors of the earlier batches with their chunks. Count what is there now.
        with session_scope(session_factory) as session:
            chunks = _count_chunks(session)
            with_vector = chunks - len(_pending_inputs(session, model_name))
        already_embedded = min(already_embedded, with_vector)
        embedded = with_vector - already_embedded
    return EmbeddingRunSummary(
        chunks=chunks,
        already_embedded=already_embedded,
        embedded=embedded,
        stop_reason=stop_reason,
    )


def _pending_inputs(session: Session, model_name: str) -> list[tuple[int, str]]:
    """(chunk row id, text to embed) for each chunk without a vector, in a repeatable order."""
    has_vector = exists().where(
        EvidenceChunkEmbedding.evidence_chunk_id == EvidenceChunk.id,
        EvidenceChunkEmbedding.model_name == model_name,
        EvidenceChunkEmbedding.input_version == EMBEDDING_INPUT_VERSION,
    )
    rows = session.execute(
        select(
            EvidenceChunk.id,
            EvidenceDocument.title,
            EvidenceChunk.section_title,
            EvidenceChunk.text,
        )
        .join(EvidenceDocument, EvidenceChunk.evidence_document_id == EvidenceDocument.id)
        .where(~has_vector)
        .order_by(
            EvidenceDocument.source,
            EvidenceDocument.source_document_id,
            EvidenceChunk.chunk_index,
        )
    )
    return [
        (chunk_id, build_embedding_input(title, section_title, text))
        for chunk_id, title, section_title, text in rows
    ]


def _answer_problem(result: EmbeddingResult, model_name: str) -> str | None:
    """Say why `result` must not be stored, or None. The text holds no value of a vector."""
    if result.model_name != model_name:
        return "the vectors were made by another model than the one asked for"
    for vector in result.vectors:
        if not _is_storable(vector):
            return f"a vector is not {EMBEDDING_DIMENSIONS} finite numbers"
    return None


def _is_storable(vector: Sequence[float]) -> bool:
    return len(vector) == EMBEDDING_DIMENSIONS and all(math.isfinite(value) for value in vector)
