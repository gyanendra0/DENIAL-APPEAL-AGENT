"""Evidence search tests, with made-up chunks and tiny made-up vectors. No real model is called.

A made-up vector is two or three chosen numbers followed by zeros, so the closeness of two
vectors can be worked out by hand: (1, 0) against (1, 0) is 1, against (1, 1) about 0.7071,
against (0, 1) it is 0 and against (-1, 0) it is -1.
"""

import hashlib
import math
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import EMBEDDING_DIMENSIONS, EvidenceSource
from src.db.session import session_scope
from src.llm.gateway import (
    LlmBudgetExceededError,
    LlmUnavailableError,
    ProviderUnavailableError,
)
from src.rag.retrieval import (
    DEFAULT_TOP_K,
    QUERY_EMBEDDING_PURPOSE,
    EvidenceMatch,
    EvidenceNotEmbeddedError,
    QueryEmbeddingError,
    search_evidence,
    search_evidence_by_vector,
)
from tests.rag.helpers import (
    CALL_COST,
    MODEL,
    StubEmbedder,
    add_document,
    embedding_calls,
    embedding_gateway,
    evidence_tables,
    padded_vector,
    store_vector,
)

QUERY = padded_vector([1.0, 0.0])
NOT_EMBEDDED = "no vector is stored for model example-embedding-model, input version v1"


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with evidence_tables(engine) as session_factory:
        yield session_factory


def _four_chunks(factory: sessionmaker[Session]) -> None:
    """Documents 9001 and 9002 with two chunks each, at closeness 1, 0.7071, 0 and -1 to QUERY."""
    with session_scope(factory) as session:
        first = add_document(session, "9001", 2)
        second = add_document(session, "9002", 2, section_title=None)
        store_vector(session, first[0], [1.0, 0.0])
        store_vector(session, first[1], [1.0, 1.0])
        store_vector(session, second[0], [0.0, 1.0])
        store_vector(session, second[1], [-1.0, 0.0])


def _search(
    factory: sessionmaker[Session],
    query: list[float] = QUERY,
    *,
    model_name: str = MODEL,
    top_k: int = DEFAULT_TOP_K,
    source: EvidenceSource | None = None,
    min_similarity: float | None = None,
) -> list[EvidenceMatch]:
    with session_scope(factory) as session:
        return search_evidence_by_vector(
            session,
            query,
            model_name=model_name,
            top_k=top_k,
            source=source,
            min_similarity=min_similarity,
        )


def _search_text(
    factory: sessionmaker[Session],
    embedder: StubEmbedder,
    query_text: str = "Is the made-up service covered?",
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[EvidenceMatch]:
    with session_scope(factory) as session:
        return search_evidence(
            session,
            embedding_gateway(factory, embedder),
            query_text,
            model_name=MODEL,
            top_k=top_k,
        )


def _places(matches: list[EvidenceMatch]) -> list[tuple[str, int]]:
    return [(match.source_document_id, match.chunk_index) for match in matches]


def _stub_answer_chunks(factory: sessionmaker[Session]) -> None:
    """Chunk 9001-0 has the vector the stub gives a first text; chunk 9001-1 is unrelated."""
    with session_scope(factory) as session:
        chunk_ids = add_document(session, "9001", 2)
        store_vector(session, chunk_ids[0], [0.0], filler=0.5)
        store_vector(session, chunk_ids[1], [1.0])


def test_the_closest_chunk_comes_first_with_its_similarity(
    factory: sessionmaker[Session],
) -> None:
    _four_chunks(factory)

    matches = _search(factory)

    assert _places(matches) == [("9001", 0), ("9001", 1), ("9002", 0), ("9002", 1)]
    assert [match.similarity for match in matches] == pytest.approx(
        [1.0, 1 / math.sqrt(2), 0.0, -1.0], abs=1e-6
    )


def test_a_match_carries_the_citation_fields_and_the_text(
    factory: sessionmaker[Session],
) -> None:
    _four_chunks(factory)

    first, *_, last = _search(factory)

    assert first.model_dump(exclude={"similarity"}) == {
        "source": EvidenceSource.CMS_NCD,
        "source_document_id": "9001",
        "chunk_index": 0,
        "text_sha256": hashlib.sha256(b"Made-up policy text 9001-0.").hexdigest(),
        "section_number": "999.9001",
        "document_title": "Made-up Determination 9001",
        "section_title": "Made-up Section",
        "text": "Made-up policy text 9001-0.",
    }
    assert last.section_title is None


def test_a_match_has_no_row_id_and_its_repr_leaves_the_text_out(
    factory: sessionmaker[Session],
) -> None:
    _four_chunks(factory)

    match = _search(factory, top_k=1)[0]

    assert set(EvidenceMatch.model_fields) == {
        "source",
        "source_document_id",
        "chunk_index",
        "text_sha256",
        "section_number",
        "document_title",
        "section_title",
        "text",
        "similarity",
    }
    assert "Made-up policy text" not in repr(match)


def test_chunks_with_the_same_similarity_come_back_by_document_then_chunk_index(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        # Stored in the opposite order, so the row ids would give 9002 first.
        later = add_document(session, "9002", 1)
        earlier = add_document(session, "9001", 2)
        for chunk_id in [*later, *reversed(earlier)]:
            store_vector(session, chunk_id, [1.0, 0.0])

    matches = _search(factory)

    assert _places(matches) == [("9001", 0), ("9001", 1), ("9002", 0)]
    assert _search(factory) == matches


def test_top_k_limits_the_matches_and_is_5_by_default(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as session:
        for chunk_id in add_document(session, "9001", 7):
            store_vector(session, chunk_id, [1.0, 0.0])

    assert DEFAULT_TOP_K == 5
    assert _places(_search(factory)) == [("9001", index) for index in range(5)]
    assert _places(_search(factory, top_k=2)) == [("9001", 0), ("9001", 1)]
    assert len(_search(factory, top_k=50)) == 7


def test_the_similarity_floor_drops_the_chunks_below_it(factory: sessionmaker[Session]) -> None:
    _four_chunks(factory)

    assert _places(_search(factory, min_similarity=0.5)) == [("9001", 0), ("9001", 1)]
    assert _places(_search(factory, min_similarity=0.9)) == [("9001", 0)]
    assert len(_search(factory, min_similarity=-1.0)) == 4


def test_a_source_filter_leaves_out_the_matches_of_the_other_source(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        determination = add_document(session, "9001", 1)
        # The manual section is the closer match, so only the filter can leave it out.
        section = add_document(session, "9002", 1, source=EvidenceSource.CMS_MANUAL)
        store_vector(session, determination[0], [1.0, 1.0])
        store_vector(session, section[0], [1.0, 0.0])

    assert _places(_search(factory)) == [("9002", 0), ("9001", 0)]
    determinations = _search(factory, source=EvidenceSource.CMS_NCD)
    sections = _search(factory, source=EvidenceSource.CMS_MANUAL)
    assert _places(determinations) == [("9001", 0)]
    assert _places(sections) == [("9002", 0)]
    assert determinations[0].source is EvidenceSource.CMS_NCD
    assert sections[0].source is EvidenceSource.CMS_MANUAL


def test_the_source_filter_is_part_of_the_database_search(
    factory: sessionmaker[Session], engine: Engine
) -> None:
    _four_chunks(factory)
    statements: list[str] = []

    def remember(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", remember)
    try:
        unfiltered = _search(factory)
        filtered = _search(factory, source=EvidenceSource.CMS_NCD)
    finally:
        event.remove(engine, "before_cursor_execute", remember)

    searches = [text for text in statements if "<=>" in text]
    assert len(searches) == 2
    assert "evidence_documents.source =" not in searches[0]
    assert "evidence_documents.source =" in searches[1]
    assert filtered == unfiltered


def test_vectors_of_another_model_or_input_version_are_never_returned(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        chunk_ids = add_document(session, "9001", 3)
        store_vector(session, chunk_ids[0], [1.0, 0.0], model_name="another-model")
        store_vector(session, chunk_ids[1], [1.0, 0.0], input_version="v0")
        store_vector(session, chunk_ids[2], [0.0, 1.0])

    assert _places(_search(factory)) == [("9001", 2)]
    assert _places(_search(factory, model_name="another-model")) == [("9001", 0)]


def test_no_match_under_the_filters_is_an_empty_list(factory: sessionmaker[Session]) -> None:
    _four_chunks(factory)

    assert _search(factory, padded_vector([0.0, 0.0, 1.0]), min_similarity=0.5) == []


def test_a_search_with_no_stored_vector_is_an_error_not_an_empty_list(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_document(session, "9001", 2)

    with pytest.raises(EvidenceNotEmbeddedError, match=NOT_EMBEDDED) as raised:
        _search(factory)

    assert "run pipelines.embed_evidence_corpus first" in str(raised.value)


def test_vectors_of_another_model_only_count_as_not_embedded(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        chunk_ids = add_document(session, "9001", 1)
        store_vector(session, chunk_ids[0], [1.0, 0.0], model_name="another-model")

    with pytest.raises(EvidenceNotEmbeddedError, match=NOT_EMBEDDED):
        _search(factory)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ([1.0, 0.0], "the query vector must be 1536 finite numbers"),
        (padded_vector([1.0]) + [0.0], "the query vector must be 1536 finite numbers"),
        (padded_vector([math.nan]), "the query vector must be 1536 finite numbers"),
        (padded_vector([math.inf]), "the query vector must be 1536 finite numbers"),
        (padded_vector([0.0]), "the query vector must hold a number that is not zero"),
    ],
    ids=["too short", "too long", "not a number", "infinite", "all zeros"],
)
def test_a_query_vector_that_cannot_be_compared_is_refused(
    factory: sessionmaker[Session], query: list[float], message: str
) -> None:
    _four_chunks(factory)

    with pytest.raises(ValueError, match=message):
        _search(factory, query)


@pytest.mark.parametrize("top_k", [0, -1])
def test_a_top_k_under_1_is_refused(factory: sessionmaker[Session], top_k: int) -> None:
    _four_chunks(factory)

    with pytest.raises(ValueError, match="top_k must be 1 or more"):
        _search(factory, top_k=top_k)


@pytest.mark.parametrize("floor", [-1.01, 1.01, math.nan])
def test_a_similarity_floor_outside_minus_1_to_1_is_refused(
    factory: sessionmaker[Session], floor: float
) -> None:
    _four_chunks(factory)

    with pytest.raises(ValueError, match="min_similarity must be between -1 and 1"):
        _search(factory, min_similarity=floor)


def test_a_search_by_vector_writes_no_spend_row(factory: sessionmaker[Session]) -> None:
    _four_chunks(factory)

    _search(factory)

    assert embedding_calls(factory, QUERY_EMBEDDING_PURPOSE) == []
    assert embedding_calls(factory) == []


def test_a_search_by_text_embeds_the_question_as_it_is_and_ranks_the_chunks(
    factory: sessionmaker[Session],
) -> None:
    _stub_answer_chunks(factory)
    embedder = StubEmbedder()

    matches = _search_text(factory, embedder, "  Is the made-up service covered?")

    assert [request.texts for request in embedder.requests] == [
        ("  Is the made-up service covered?",)
    ]
    assert [(request.input_version, request.purpose) for request in embedder.requests] == [
        ("v1", "query_embedding")
    ]
    assert _places(matches) == [("9001", 0), ("9001", 1)]
    assert matches[0].similarity == pytest.approx(1.0, abs=1e-6)
    assert _places(_search_text(factory, StubEmbedder(), top_k=1)) == [("9001", 0)]


def test_a_search_by_text_writes_one_spend_row(factory: sessionmaker[Session]) -> None:
    _stub_answer_chunks(factory)

    _search_text(factory, StubEmbedder())

    calls = embedding_calls(factory, QUERY_EMBEDDING_PURPOSE)
    assert [
        (call.model_name, call.prompt_version, call.output_tokens, call.cost_usd) for call in calls
    ] == [(MODEL, "v1", 0, CALL_COST)]


def test_a_search_by_text_with_no_stored_vector_spends_nothing(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_document(session, "9001", 2)
    embedder = StubEmbedder()

    with pytest.raises(EvidenceNotEmbeddedError, match=NOT_EMBEDDED):
        _search_text(factory, embedder)

    assert embedder.requests == []
    assert embedding_calls(factory, QUERY_EMBEDDING_PURPOSE) == []


@pytest.mark.parametrize("query_text", ["", "   ", "\n\t"])
def test_a_question_with_no_text_is_refused_before_any_call(
    factory: sessionmaker[Session], query_text: str
) -> None:
    _stub_answer_chunks(factory)
    embedder = StubEmbedder()

    with pytest.raises(ValueError, match="the query text must not be empty"):
        _search_text(factory, embedder, query_text)

    assert embedder.requests == []


def test_bad_filters_are_refused_before_the_question_is_embedded(
    factory: sessionmaker[Session],
) -> None:
    _stub_answer_chunks(factory)
    embedder = StubEmbedder()

    with pytest.raises(ValueError, match="top_k must be 1 or more"):
        _search_text(factory, embedder, top_k=0)

    assert embedder.requests == []


def test_over_budget_the_question_is_not_embedded(factory: sessionmaker[Session]) -> None:
    _stub_answer_chunks(factory)
    embedder = StubEmbedder(highest_cost="5.00")

    with pytest.raises(LlmBudgetExceededError):
        _search_text(factory, embedder)

    assert embedder.requests == []


def test_an_unavailable_provider_leaves_the_search_as_the_gateways_error(
    factory: sessionmaker[Session],
) -> None:
    _stub_answer_chunks(factory)
    embedder = StubEmbedder(errors={1: ProviderUnavailableError("made-up outage")})

    with pytest.raises(LlmUnavailableError):
        _search_text(factory, embedder)

    assert embedding_calls(factory, QUERY_EMBEDDING_PURPOSE) == []


def test_an_answer_from_another_model_is_not_searched_with(
    factory: sessionmaker[Session],
) -> None:
    _stub_answer_chunks(factory)

    with pytest.raises(QueryEmbeddingError, match="made by another model"):
        _search_text(factory, StubEmbedder(model_name="another-model"))


@pytest.mark.parametrize(
    ("embedder", "message"),
    [
        (StubEmbedder(dimensions=3), "the vector of the query text must be 1536 finite numbers"),
        (StubEmbedder(filler={1: math.nan}), "must be 1536 finite numbers"),
        (StubEmbedder(filler={1: 0.0}), "must hold a number that is not zero"),
    ],
    ids=["wrong size", "not a number", "all zeros"],
)
def test_an_answer_that_is_not_a_usable_vector_is_an_error_after_the_spend_is_recorded(
    factory: sessionmaker[Session], embedder: StubEmbedder, message: str
) -> None:
    _stub_answer_chunks(factory)

    with pytest.raises(QueryEmbeddingError, match=message):
        _search_text(factory, embedder)

    assert len(embedding_calls(factory, QUERY_EMBEDDING_PURPOSE)) == 1
    assert EMBEDDING_DIMENSIONS == 1536
