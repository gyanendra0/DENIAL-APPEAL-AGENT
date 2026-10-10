"""Made-up evidence chunks and a stub embedding provider for the embedding tests.

The gateway commits in its own sessions, so the made-up rows are really stored and removed
afterwards. No real model is called and no real policy text is used.
"""

import hashlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    EMBEDDING_DIMENSIONS,
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
    EvidenceSource,
    LlmCall,
)
from src.db.session import create_session_factory, session_scope
from src.llm.gateway import EmbeddingAnswer, EmbeddingRequest, LlmGateway
from src.rag.embedding_input import EMBEDDING_INPUT_VERSION
from src.rag.embedding_run import EMBEDDING_RUN_LOCK_KEY, EVIDENCE_EMBEDDING_PURPOSE
from src.rag.retrieval import QUERY_EMBEDDING_PURPOSE
from tests.extraction.helpers import CAP, StubProvider

MODEL = "example-embedding-model"
DOCUMENT_TITLE = "Made-up Determination"
SECTION_TITLE = "Made-up Section"
CHUNK_TEXT = "Made-up policy text"
CALL_COST = Decimal("0.000004")
# Quarters are stored exactly in the column's four-byte numbers.
VECTOR_STEP = 0.25


class StubEmbedder:
    """An embedding provider that numbers its vectors and remembers the requests it was sent.

    The first number of a vector is `VECTOR_STEP` times the count of texts answered before
    it, so a test can tell which stored vector belongs to which text.
    """

    def __init__(
        self,
        model_name: str = MODEL,
        *,
        highest_cost: str = "0.01",
        dimensions: int = EMBEDDING_DIMENSIONS,
        errors: Mapping[int, Exception] | None = None,
        filler: Mapping[int, float] | None = None,
        short_by: Mapping[int, int] | None = None,
        before_answer: Mapping[int, Callable[[], None]] | None = None,
    ) -> None:
        self._model_name = model_name
        self._highest_cost = Decimal(highest_cost)
        self._dimensions = dimensions
        # Each keyed by the number of the call, counted from 1.
        self._errors = errors or {}
        self._filler = filler or {}
        self._short_by = short_by or {}
        # Run while the call is "at the provider", to change the database under the caller.
        self._before_answer = before_answer or {}
        self._answered = 0
        self.requests: list[EmbeddingRequest] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def highest_cost(self, request: EmbeddingRequest) -> Decimal:
        return self._highest_cost

    def run(self, request: EmbeddingRequest) -> EmbeddingAnswer:
        self.requests.append(request)
        call = len(self.requests)
        if call in self._errors:
            raise self._errors[call]
        if call in self._before_answer:
            self._before_answer[call]()
        filler = self._filler.get(call, 0.5)
        vectors = []
        for _ in request.texts[: len(request.texts) - self._short_by.get(call, 0)]:
            vectors.append((self._answered * VECTOR_STEP, *[filler] * (self._dimensions - 1)))
            self._answered += 1
        return EmbeddingAnswer(vectors=tuple(vectors), input_tokens=21, cost_usd=CALL_COST)


def embedding_gateway(factory: sessionmaker[Session], embedder: StubEmbedder) -> LlmGateway:
    return LlmGateway(
        primary=StubProvider(),
        fallback=None,
        session_factory=factory,
        cap_usd=CAP,
        embedder=embedder,
    )


def add_document(
    session: Session,
    source_document_id: str,
    chunk_count: int,
    *,
    section_title: str | None = SECTION_TITLE,
) -> list[int]:
    """Store one made-up document with `chunk_count` chunks; return the chunk row ids."""
    document = EvidenceDocument(
        source=EvidenceSource.CMS_NCD,
        source_document_id=source_document_id,
        section_number=f"999.{source_document_id}",
        title=f"{DOCUMENT_TITLE} {source_document_id}",
        version_number=1,
        effective_date=date(2020, 1, 1),
        source_file_date=date(2026, 10, 5),
    )
    session.add(document)
    session.flush()
    chunks = []
    for index in range(chunk_count):
        text = f"{CHUNK_TEXT} {source_document_id}-{index}."
        chunks.append(
            EvidenceChunk(
                evidence_document_id=document.id,
                chunk_index=index,
                section_title=section_title,
                text=text,
                text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                chunker_version="v1",
            )
        )
    session.add_all(chunks)
    session.flush()
    return [chunk.id for chunk in chunks]


def delete_chunk(factory: sessionmaker[Session], chunk_id: int) -> None:
    """Remove one chunk, and with it its vector, as a new load of the corpus would."""
    with session_scope(factory) as session:
        session.execute(delete(EvidenceChunk).where(EvidenceChunk.id == chunk_id))


@contextmanager
def evidence_tables(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Yield a committing session factory; remove the evidence rows and the calls after."""
    factory = create_session_factory(engine)
    _remove(factory)  # in case an earlier run was killed before its clean-up
    try:
        yield factory
    finally:
        _remove(factory)


def _remove(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as session:
        # The chunks and their vectors go with the documents.
        session.execute(delete(EvidenceDocument))
        session.execute(
            delete(LlmCall).where(
                LlmCall.purpose.in_([EVIDENCE_EMBEDDING_PURPOSE, QUERY_EMBEDDING_PURPOSE])
            )
        )


def stored_vectors(factory: sessionmaker[Session]) -> list[tuple[int, str, str, float, int]]:
    """(chunk id, model, input version, first number, size) per stored vector, by chunk id."""
    with session_scope(factory) as session:
        rows = session.scalars(
            select(EvidenceChunkEmbedding).order_by(
                EvidenceChunkEmbedding.evidence_chunk_id, EvidenceChunkEmbedding.model_name
            )
        )
        return [
            (
                row.evidence_chunk_id,
                row.model_name,
                row.input_version,
                float(row.embedding[0]),
                len(row.embedding),
            )
            for row in rows
        ]


def embedding_calls(
    factory: sessionmaker[Session], purpose: str = EVIDENCE_EMBEDDING_PURPOSE
) -> list[LlmCall]:
    with session_scope(factory) as session:
        return list(
            session.scalars(select(LlmCall).where(LlmCall.purpose == purpose).order_by(LlmCall.id))
        )


def padded_vector(head: Sequence[float], filler: float = 0.0) -> list[float]:
    """A full-size vector: `head`, then `filler` up to `EMBEDDING_DIMENSIONS` numbers."""
    return [*head, *[filler] * (EMBEDDING_DIMENSIONS - len(head))]


def store_vector(
    session: Session,
    chunk_id: int,
    head: Sequence[float],
    *,
    filler: float = 0.0,
    model_name: str = MODEL,
    input_version: str = EMBEDDING_INPUT_VERSION,
) -> None:
    """Store a made-up vector for a chunk: `head`, then `filler` up to the full size."""
    session.add(
        EvidenceChunkEmbedding(
            evidence_chunk_id=chunk_id,
            model_name=model_name,
            input_version=input_version,
            embedding=padded_vector(head, filler),
        )
    )


@contextmanager
def run_lock_held(engine: Engine) -> Iterator[bool]:
    """Hold the embedding run's lock on a connection of its own, as a second run would.

    Yields whether the lock was free to take.
    """
    with engine.connect() as connection:
        acquired = bool(
            connection.scalar(select(func.pg_try_advisory_lock(EMBEDDING_RUN_LOCK_KEY)))
        )
        try:
            yield acquired
        finally:
            if acquired:
                connection.scalar(select(func.pg_advisory_unlock(EMBEDDING_RUN_LOCK_KEY)))
