"""Embedding run tests, with a stub provider and made-up chunks. No real model is called."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    EMBEDDING_DIMENSIONS,
    EvidenceChunk,
    EvidenceChunkEmbedding,
    EvidenceDocument,
)
from src.db.session import session_scope
from src.llm.gateway import ProviderRejectedError, ProviderUnavailableError
from src.rag.embedding_run import (
    EMBEDDING_BATCH_SIZE,
    EmbeddingRunSummary,
    run_evidence_embedding,
)
from tests.rag.helpers import (
    CALL_COST,
    MODEL,
    VECTOR_STEP,
    StubEmbedder,
    add_document,
    delete_chunk,
    embedding_calls,
    embedding_gateway,
    evidence_tables,
    run_lock_held,
    stored_vectors,
)


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with evidence_tables(engine) as session_factory:
        yield session_factory


def _five_chunks(factory: sessionmaker[Session]) -> list[int]:
    """Three chunks of document 9001, then two of document 9002; their row ids in that order."""
    with session_scope(factory) as session:
        return add_document(session, "9001", 3) + add_document(
            session, "9002", 2, section_title=None
        )


def _run(
    factory: sessionmaker[Session], embedder: StubEmbedder, batch_size: int = 2
) -> EmbeddingRunSummary:
    return run_evidence_embedding(
        factory, embedding_gateway(factory, embedder), model_name=MODEL, batch_size=batch_size
    )


def test_every_chunk_gets_one_vector_stored_with_the_model_and_the_input_version(
    factory: sessionmaker[Session],
) -> None:
    chunk_ids = _five_chunks(factory)
    embedder = StubEmbedder()

    summary = _run(factory, embedder)

    assert summary == EmbeddingRunSummary(
        chunks=5, already_embedded=0, embedded=5, stop_reason=None
    )
    assert summary.not_tried == 0
    # The stub numbers its vectors in the order the texts were sent: document, then index.
    assert stored_vectors(factory) == [
        (chunk_id, MODEL, "v1", position * VECTOR_STEP, EMBEDDING_DIMENSIONS)
        for position, chunk_id in enumerate(chunk_ids)
    ]


def test_the_chunks_are_sent_in_batches_with_the_titles_before_the_text(
    factory: sessionmaker[Session],
) -> None:
    _five_chunks(factory)
    embedder = StubEmbedder()

    _run(factory, embedder)

    assert [request.texts for request in embedder.requests] == [
        (
            "Made-up Determination 9001\n\nMade-up Section\n\nMade-up policy text 9001-0.",
            "Made-up Determination 9001\n\nMade-up Section\n\nMade-up policy text 9001-1.",
        ),
        (
            "Made-up Determination 9001\n\nMade-up Section\n\nMade-up policy text 9001-2.",
            "Made-up Determination 9002\n\nMade-up policy text 9002-0.",
        ),
        ("Made-up Determination 9002\n\nMade-up policy text 9002-1.",),
    ]
    assert {(request.input_version, request.purpose) for request in embedder.requests} == {
        ("v1", "evidence_embedding")
    }


def test_each_batch_writes_one_spend_row(factory: sessionmaker[Session]) -> None:
    _five_chunks(factory)

    _run(factory, StubEmbedder())

    calls = embedding_calls(factory)
    assert [(call.model_name, call.prompt_version, call.output_tokens) for call in calls] == [
        (MODEL, "v1", 0)
    ] * 3
    assert sum(call.cost_usd for call in calls) == 3 * CALL_COST


def test_a_batch_holds_100_chunks_by_default(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as session:
        add_document(session, "9001", 101)
    embedder = StubEmbedder()

    summary = run_evidence_embedding(
        factory, embedding_gateway(factory, embedder), model_name=MODEL
    )

    assert EMBEDDING_BATCH_SIZE == 100
    assert [len(request.texts) for request in embedder.requests] == [100, 1]
    assert summary.embedded == 101
    assert len(stored_vectors(factory)) == 101


def test_a_second_run_embeds_nothing_and_calls_nothing(factory: sessionmaker[Session]) -> None:
    _five_chunks(factory)
    _run(factory, StubEmbedder())
    second = StubEmbedder()

    summary = _run(factory, second)

    assert summary == EmbeddingRunSummary(
        chunks=5, already_embedded=5, embedded=0, stop_reason=None
    )
    assert second.requests == []
    assert len(embedding_calls(factory)) == 3
    assert len(stored_vectors(factory)) == 5


def test_only_the_chunks_without_a_vector_are_sent(factory: sessionmaker[Session]) -> None:
    chunk_ids = _five_chunks(factory)
    _run(factory, StubEmbedder())
    with session_scope(factory) as session:
        session.execute(
            delete(EvidenceChunkEmbedding).where(
                EvidenceChunkEmbedding.evidence_chunk_id == chunk_ids[3]
            )
        )
    embedder = StubEmbedder()

    summary = _run(factory, embedder)

    assert summary == EmbeddingRunSummary(
        chunks=5, already_embedded=4, embedded=1, stop_reason=None
    )
    assert [request.texts for request in embedder.requests] == [
        ("Made-up Determination 9002\n\nMade-up policy text 9002-0.",)
    ]


def test_a_vector_of_another_model_does_not_count_as_embedded(
    factory: sessionmaker[Session],
) -> None:
    _five_chunks(factory)
    other = StubEmbedder("another-embedding-model")
    run_evidence_embedding(
        factory, embedding_gateway(factory, other), model_name="another-embedding-model"
    )

    summary = _run(factory, StubEmbedder())

    assert summary == EmbeddingRunSummary(
        chunks=5, already_embedded=0, embedded=5, stop_reason=None
    )
    assert [model for _, model, _, _, _ in stored_vectors(factory)] == [
        "another-embedding-model",
        MODEL,
    ] * 5


def test_a_vector_of_another_input_version_does_not_count_as_embedded(
    factory: sessionmaker[Session],
) -> None:
    chunk_ids = _five_chunks(factory)
    with session_scope(factory) as session:
        session.add(
            EvidenceChunkEmbedding(
                evidence_chunk_id=chunk_ids[0],
                model_name=MODEL,
                input_version="v0",
                embedding=[0.5] * EMBEDDING_DIMENSIONS,
            )
        )

    summary = _run(factory, StubEmbedder())

    assert summary.already_embedded == 0
    assert summary.embedded == 5


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ProviderUnavailableError("timeout"), "the embedding provider could not answer"),
        (
            ProviderRejectedError("AuthenticationError (status 401)"),
            "the provider refused the request: AuthenticationError (status 401)",
        ),
    ],
)
def test_a_failed_batch_stops_the_run_and_keeps_the_earlier_batches(
    factory: sessionmaker[Session], error: Exception, reason: str
) -> None:
    chunk_ids = _five_chunks(factory)
    embedder = StubEmbedder(errors={2: error})

    summary = _run(factory, embedder)

    assert summary == EmbeddingRunSummary(
        chunks=5, already_embedded=0, embedded=2, stop_reason=reason
    )
    assert summary.not_tried == 3
    assert len(embedder.requests) == 2  # the third batch was not tried
    assert [chunk_id for chunk_id, *_ in stored_vectors(factory)] == chunk_ids[:2]


def test_a_stopped_run_is_continued_by_the_next_run(factory: sessionmaker[Session]) -> None:
    chunk_ids = _five_chunks(factory)
    _run(factory, StubEmbedder(errors={2: ProviderUnavailableError("timeout")}))
    embedder = StubEmbedder()

    summary = _run(factory, embedder)

    assert summary == EmbeddingRunSummary(
        chunks=5, already_embedded=2, embedded=3, stop_reason=None
    )
    assert [len(request.texts) for request in embedder.requests] == [2, 1]
    assert [chunk_id for chunk_id, *_ in stored_vectors(factory)] == chunk_ids


def test_over_budget_stops_the_run_before_the_provider_is_called(
    factory: sessionmaker[Session],
) -> None:
    _five_chunks(factory)
    embedder = StubEmbedder(highest_cost="5.00")

    summary = _run(factory, embedder)

    assert summary == EmbeddingRunSummary(
        chunks=5, already_embedded=0, embedded=0, stop_reason="the monthly LLM budget is reached"
    )
    assert embedder.requests == []
    assert embedding_calls(factory) == []
    assert stored_vectors(factory) == []


@pytest.mark.parametrize(
    "embedder",
    [
        StubEmbedder(dimensions=3),
        StubEmbedder(dimensions=EMBEDDING_DIMENSIONS - 1),
        StubEmbedder(dimensions=EMBEDDING_DIMENSIONS + 1),
    ],
    ids=["3", "one short", "one long"],
)
def test_a_vector_of_another_size_is_refused_before_anything_is_stored(
    factory: sessionmaker[Session], embedder: StubEmbedder
) -> None:
    _five_chunks(factory)

    summary = _run(factory, embedder)

    assert summary.embedded == 0
    assert summary.stop_reason == (
        "the answer cannot be stored (the call was paid for):"
        f" a vector is not {EMBEDDING_DIMENSIONS} finite numbers"
    )
    assert len(embedder.requests) == 1
    assert len(embedding_calls(factory)) == 1  # the call was paid for and is recorded
    assert stored_vectors(factory) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_vector_with_a_number_that_is_not_finite_stores_nothing_of_its_batch(
    factory: sessionmaker[Session], value: float
) -> None:
    chunk_ids = _five_chunks(factory)

    summary = _run(factory, StubEmbedder(filler={2: value}))

    assert summary.embedded == 2
    assert summary.stop_reason is not None
    assert summary.stop_reason.endswith(f"a vector is not {EMBEDDING_DIMENSIONS} finite numbers")
    assert [chunk_id for chunk_id, *_ in stored_vectors(factory)] == chunk_ids[:2]


def test_an_answer_with_a_vector_missing_stores_nothing_of_its_batch(
    factory: sessionmaker[Session],
) -> None:
    _five_chunks(factory)

    summary = _run(factory, StubEmbedder(short_by={1: 1}))

    assert summary.embedded == 0
    assert summary.stop_reason == (
        "the answer cannot be stored (the call was paid for):"
        " the provider returned 1 vectors for 2 texts"
    )
    assert stored_vectors(factory) == []


def test_vectors_of_another_model_than_the_one_asked_for_are_not_stored(
    factory: sessionmaker[Session],
) -> None:
    _five_chunks(factory)
    embedder = StubEmbedder("another-embedding-model")

    summary = _run(factory, embedder)

    assert summary.embedded == 0
    assert summary.stop_reason == (
        "the answer cannot be stored (the call was paid for):"
        " the vectors were made by another model than the one asked for"
    )
    assert len(embedder.requests) == 1
    assert stored_vectors(factory) == []


def test_a_chunk_removed_during_the_run_stops_it_and_stores_nothing_of_that_batch(
    factory: sessionmaker[Session],
) -> None:
    chunk_ids = _five_chunks(factory)
    # The fourth chunk goes while the second batch (chunks three and four) is at the provider.
    embedder = StubEmbedder(before_answer={2: lambda: delete_chunk(factory, chunk_ids[3])})

    summary = _run(factory, embedder)

    # The counts are those after the change: four chunks are left, two of them with a vector.
    assert summary == EmbeddingRunSummary(
        chunks=4,
        already_embedded=0,
        embedded=2,
        stop_reason="the evidence chunks changed during the run (the call was paid for)",
    )
    assert summary.not_tried == 2
    assert len(embedder.requests) == 2  # the third batch was not tried
    assert len(embedding_calls(factory)) == 2  # the second call was paid for and is recorded
    assert [chunk_id for chunk_id, *_ in stored_vectors(factory)] == chunk_ids[:2]


def test_a_corpus_replaced_during_the_run_counts_the_new_chunks_and_no_vector(
    factory: sessionmaker[Session],
) -> None:
    _five_chunks(factory)

    def replace_corpus() -> None:
        # As a new load does: every document goes, and its chunks and vectors with it.
        with session_scope(factory) as session:
            session.execute(delete(EvidenceDocument))
            add_document(session, "9003", 3)

    summary = _run(factory, StubEmbedder(before_answer={2: replace_corpus}))

    # The first batch was stored and is gone again with its chunks.
    assert summary == EmbeddingRunSummary(
        chunks=3,
        already_embedded=0,
        embedded=0,
        stop_reason="the evidence chunks changed during the run (the call was paid for)",
    )
    assert summary.not_tried == 3
    assert stored_vectors(factory) == []
    assert len(embedding_calls(factory)) == 2


def test_no_stored_chunk_means_no_call(factory: sessionmaker[Session]) -> None:
    embedder = StubEmbedder()

    summary = _run(factory, embedder)

    assert summary == EmbeddingRunSummary(
        chunks=0, already_embedded=0, embedded=0, stop_reason=None
    )
    assert embedder.requests == []


def _chunk_texts(factory: sessionmaker[Session]) -> list[tuple[int, str, str]]:
    with session_scope(factory) as session:
        rows = session.execute(
            select(EvidenceChunk.id, EvidenceChunk.text, EvidenceChunk.text_sha256).order_by(
                EvidenceChunk.id
            )
        )
        return [(row[0], row[1], row[2]) for row in rows]


def test_the_run_leaves_the_chunk_text_and_its_hash_as_they_were(
    factory: sessionmaker[Session],
) -> None:
    _five_chunks(factory)
    before = _chunk_texts(factory)

    _run(factory, StubEmbedder())

    assert len(before) == 5
    assert _chunk_texts(factory) == before


def test_a_batch_size_below_one_is_refused(factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="batch_size must be 1 or more"):
        _run(factory, StubEmbedder(), batch_size=0)


def test_a_run_stops_before_any_call_while_another_run_is_in_progress(
    factory: sessionmaker[Session], engine: Engine
) -> None:
    _five_chunks(factory)
    embedder = StubEmbedder()

    with run_lock_held(engine) as other_run_has_the_lock:
        summary = _run(factory, embedder)

    assert other_run_has_the_lock
    assert summary == EmbeddingRunSummary(
        chunks=5,
        already_embedded=0,
        embedded=0,
        stop_reason="another embedding run is in progress",
    )
    assert summary.not_tried == 5
    assert embedder.requests == []
    assert embedding_calls(factory) == []
    assert stored_vectors(factory) == []


def test_a_run_refused_for_another_run_leaves_that_runs_lock_alone(
    factory: sessionmaker[Session], engine: Engine
) -> None:
    _five_chunks(factory)

    with run_lock_held(engine):
        _run(factory, StubEmbedder())
        # Still held by the first holder: a third run is refused too.
        assert _run(factory, StubEmbedder()).stop_reason == "another embedding run is in progress"


@pytest.mark.parametrize(
    "embedder",
    [
        StubEmbedder(),
        StubEmbedder(errors={2: ProviderUnavailableError("timeout")}),
        StubEmbedder(highest_cost="5.00"),
    ],
    ids=["finished", "stopped early", "over budget"],
)
def test_the_lock_is_free_again_after_a_run(
    factory: sessionmaker[Session], engine: Engine, embedder: StubEmbedder
) -> None:
    _five_chunks(factory)

    _run(factory, embedder)

    with run_lock_held(engine) as free:
        assert free


def test_the_lock_is_free_again_after_a_run_that_raised(
    factory: sessionmaker[Session], engine: Engine
) -> None:
    _five_chunks(factory)

    with pytest.raises(RuntimeError, match="made-up failure"):
        _run(factory, StubEmbedder(errors={1: RuntimeError("made-up failure")}))

    with run_lock_held(engine) as free:
        assert free


def test_a_run_right_after_another_embeds_what_is_left(
    factory: sessionmaker[Session], engine: Engine
) -> None:
    _five_chunks(factory)
    with run_lock_held(engine):
        _run(factory, StubEmbedder())

    summary = _run(factory, StubEmbedder())

    assert summary == EmbeddingRunSummary(
        chunks=5, already_embedded=0, embedded=5, stop_reason=None
    )
