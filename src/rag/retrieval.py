"""Searches the embedded evidence chunks for the ones closest in meaning to a question.

Two entry points. `search_evidence_by_vector` takes a vector and only reads the database: no
model call, no money. `search_evidence` takes the question as text, embeds it through the LLM
gateway (a small paid call) and then runs the same database search.

A result names its chunk by `source`, `source_document_id`, `chunk_index` and `text_sha256`,
the fields a citation uses. It never carries a row id: a new load of the corpus gives every
row a new id.

The database compares the question with every stored vector (no index), so the answer is
exact. Only vectors of the asked model and of `EMBEDDING_INPUT_VERSION` are compared: vectors
made by two models, or from two input recipes, say nothing about each other.
"""

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from src.db.models import (
    EMBEDDING_DIMENSIONS,
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
    EvidenceSource,
)
from src.llm.gateway import EmbeddingRequest, LlmGateway
from src.rag.embedding_input import EMBEDDING_INPUT_VERSION

# The number of chunks a search returns when the caller does not say.
DEFAULT_TOP_K = 5
# Stored in `llm_calls.purpose` with the call that embeds a question.
QUERY_EMBEDDING_PURPOSE = "query_embedding"
# The range of a cosine similarity: -1 opposite meaning, 0 unrelated, 1 the same direction.
LOWEST_SIMILARITY = -1.0
HIGHEST_SIMILARITY = 1.0


class EvidenceNotEmbeddedError(Exception):
    """No vector is stored for the asked model and input version, so nothing can be searched.

    Raised so that "the chunks were never embedded" (or a new corpus load removed the vectors)
    cannot be mistaken for "no evidence fits the question", which is an empty list.
    """


class QueryEmbeddingError(Exception):
    """The answer to a question's embedding call cannot be used for a search.

    The call was paid for and its spend is recorded before this is raised.
    """


class EvidenceMatch(BaseModel):
    """One chunk a search found, with the fields a citation needs and how close it is.

    `source`, `source_document_id`, `chunk_index` and `text_sha256` name the chunk in a
    citation. `similarity` is the cosine similarity between the question and the chunk's
    vector: 1 is the same direction, 0 unrelated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EvidenceSource
    source_document_id: str
    chunk_index: int = Field(ge=0)
    text_sha256: str
    section_number: str
    document_title: str
    section_title: str | None
    # Left out of the repr: a chunk can be 1,200 characters long.
    text: str = Field(repr=False)
    similarity: float


def search_evidence_by_vector(
    session: Session,
    query_vector: Sequence[float],
    *,
    model_name: str,
    top_k: int = DEFAULT_TOP_K,
    source: EvidenceSource | None = None,
    min_similarity: float | None = None,
) -> list[EvidenceMatch]:
    """Return the `top_k` stored chunks closest to `query_vector`, the closest first.

    Reads the database only: no model call, no money. `query_vector` must come from
    `model_name` with the input recipe `EMBEDDING_INPUT_VERSION`; only vectors of that pair
    are compared. `source` keeps the chunks of one source, `min_similarity` drops the chunks
    below that cosine similarity. Chunks with the same similarity come back by source, then
    `source_document_id`, then `chunk_index`, so the order is repeatable.

    An empty list means no chunk passed the filters. Raises `EvidenceNotEmbeddedError` when
    no vector is stored for the model and input version at all, and `ValueError` for a
    `top_k` under 1, a `min_similarity` outside -1 to 1, or a `query_vector` that is not
    `EMBEDDING_DIMENSIONS` finite numbers with at least one that is not zero.
    """
    _check_filters(top_k, min_similarity)
    problem = _vector_problem(query_vector)
    if problem is not None:
        raise ValueError(f"the query vector {problem}")

    distance = EvidenceChunkEmbedding.embedding.cosine_distance(list(query_vector))
    statement = (
        select(
            EvidenceDocument.source,
            EvidenceDocument.source_document_id,
            EvidenceChunk.chunk_index,
            EvidenceChunk.text_sha256,
            EvidenceDocument.section_number,
            EvidenceDocument.title,
            EvidenceChunk.section_title,
            EvidenceChunk.text,
            (1 - distance).label("similarity"),
        )
        .join(EvidenceChunk, EvidenceChunkEmbedding.evidence_chunk_id == EvidenceChunk.id)
        .join(EvidenceDocument, EvidenceChunk.evidence_document_id == EvidenceDocument.id)
        .where(
            EvidenceChunkEmbedding.model_name == model_name,
            EvidenceChunkEmbedding.input_version == EMBEDDING_INPUT_VERSION,
        )
        .order_by(
            distance,
            EvidenceDocument.source,
            EvidenceDocument.source_document_id,
            EvidenceChunk.chunk_index,
        )
        .limit(top_k)
    )
    if source is not None:
        statement = statement.where(EvidenceDocument.source == source)
    if min_similarity is not None:
        statement = statement.where(distance <= 1 - min_similarity)

    matches = [
        EvidenceMatch(
            source=row_source,
            source_document_id=source_document_id,
            chunk_index=chunk_index,
            text_sha256=text_sha256,
            section_number=section_number,
            document_title=title,
            section_title=section_title,
            text=text,
            similarity=similarity,
        )
        for (
            row_source,
            source_document_id,
            chunk_index,
            text_sha256,
            section_number,
            title,
            section_title,
            text,
            similarity,
        ) in session.execute(statement)
    ]
    if not matches:
        # Only asked when nothing came back, so a normal search costs one query.
        _require_vectors(session, model_name)
    return matches


def search_evidence(
    session: Session,
    gateway: LlmGateway,
    query_text: str,
    *,
    model_name: str,
    top_k: int = DEFAULT_TOP_K,
    source: EvidenceSource | None = None,
    min_similarity: float | None = None,
) -> list[EvidenceMatch]:
    """Return the `top_k` stored chunks closest in meaning to `query_text`, the closest first.

    Spends money and can take a second: one embedding call for the question, through the
    gateway and its budget check. `model_name` must be the model the gateway embeds with.
    The question is embedded as it is; the filters and the order are those of
    `search_evidence_by_vector`.

    Nothing is spent when the arguments are refused (`ValueError`, also for a question with
    no text) or when no vector is stored to search (`EvidenceNotEmbeddedError`). The gateway's
    errors leave as they are: `LlmBudgetExceededError`, `LlmUnavailableError`,
    `ProviderRejectedError`, `EmbeddingAnswerError`. Raises `QueryEmbeddingError` when the
    answer came from another model than `model_name` or is not a usable vector.
    """
    _check_filters(top_k, min_similarity)
    if not query_text.strip():
        raise ValueError("the query text must not be empty")
    _require_vectors(session, model_name)

    result = gateway.embed(
        EmbeddingRequest(
            texts=(query_text,),
            input_version=EMBEDDING_INPUT_VERSION,
            purpose=QUERY_EMBEDDING_PURPOSE,
        )
    )
    if result.model_name != model_name:
        raise QueryEmbeddingError("the vector was made by another model than the one asked for")
    query_vector = result.vectors[0]
    problem = _vector_problem(query_vector)
    if problem is not None:
        raise QueryEmbeddingError(f"the vector of the query text {problem}")
    return search_evidence_by_vector(
        session,
        query_vector,
        model_name=model_name,
        top_k=top_k,
        source=source,
        min_similarity=min_similarity,
    )


def _check_filters(top_k: int, min_similarity: float | None) -> None:
    if top_k < 1:
        raise ValueError("top_k must be 1 or more")
    if min_similarity is not None and not (
        LOWEST_SIMILARITY <= min_similarity <= HIGHEST_SIMILARITY
    ):
        raise ValueError("min_similarity must be between -1 and 1")


def _vector_problem(vector: Sequence[float]) -> str | None:
    """Say why `vector` cannot be searched with, or None. The text holds no value of it."""
    if len(vector) != EMBEDDING_DIMENSIONS or not all(math.isfinite(value) for value in vector):
        return f"must be {EMBEDDING_DIMENSIONS} finite numbers"
    if not any(vector):
        # A vector of zeros points nowhere, so it has no angle to any other vector.
        return "must hold a number that is not zero"
    return None


def _require_vectors(session: Session, model_name: str) -> None:
    has_vectors = session.scalar(
        select(
            exists().where(
                EvidenceChunkEmbedding.model_name == model_name,
                EvidenceChunkEmbedding.input_version == EMBEDDING_INPUT_VERSION,
            )
        )
    )
    if not has_vectors:
        raise EvidenceNotEmbeddedError(
            f"no vector is stored for model {model_name}, input version "
            f"{EMBEDDING_INPUT_VERSION}: run pipelines.embed_evidence_corpus first"
        )
