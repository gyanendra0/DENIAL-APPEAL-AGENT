"""Embeds the stored evidence chunks, a batch at a time, and saves each batch as it arrives.

A chunk that already has a vector for the model and the input version is skipped: a run that
stopped half way is continued, not paid for twice. A new load of the evidence corpus deletes
the chunks and with them the vectors, so this run is needed again afterwards.
"""

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, func, select
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
    texts, or a vector that is not `EMBEDDING_DIMENSIONS` finite numbers. Nothing of such a
    batch is stored.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be 1 or more")
    with session_scope(session_factory) as session:
        chunks = session.scalar(select(func.count()).select_from(EvidenceChunk)) or 0
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
        embedded += len(batch)
    return EmbeddingRunSummary(
        chunks=chunks,
        already_embedded=chunks - len(pending),
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
