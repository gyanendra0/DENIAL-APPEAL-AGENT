"""CLI tests. The gateway commits, so the made-up rows are removed afterwards.

The gateway is built from a stub embedding provider: no real model is called and no network
is used. The stub's question vectors point the same way as the vectors stored for document
9001, so its chunks always come back first.
"""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from pipelines import check_evidence_search
from pipelines.check_evidence_search import main
from src.config.settings import LlmGatewaySettings, get_settings, load_llm_gateway_settings
from src.db.models import EvidenceSource
from src.db.session import session_scope
from src.llm.gateway import LlmGateway, ProviderRejectedError, ProviderUnavailableError
from src.rag.retrieval import QUERY_EMBEDDING_PURPOSE
from tests.pipelines.test_embed_evidence_corpus import LLM_ENV, LLM_FALLBACK_KEYS
from tests.rag.helpers import (
    MODEL,
    StubEmbedder,
    add_document,
    embedding_calls,
    embedding_gateway,
    evidence_tables,
    store_vector,
)

FIXTURE = Path(__file__).parents[1] / "evaluation" / "fixtures" / "search_questions.json"
QUESTION = "Is the made-up service covered at home?"
SPENT_NOTHING = "  spent $0 in this run; $0 of the $3.00 monthly budget is used"


class Stubs:
    """The stub provider the command's gateway is built from; a test may swap it."""

    def __init__(self) -> None:
        self.embedder = StubEmbedder()


@pytest.fixture
def factory(
    engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    """Point the command at the test database, with made-up gateway settings."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    for name, value in LLM_ENV.items():
        monkeypatch.setenv(name, value)
    for name in LLM_FALLBACK_KEYS:
        monkeypatch.delenv(name, raising=False)
    # No `.env` file is read, so the test never sees a real key.
    monkeypatch.setattr(
        check_evidence_search,
        "load_llm_gateway_settings",
        lambda: load_llm_gateway_settings(env_file=None),
    )
    get_settings.cache_clear()
    with evidence_tables(engine) as session_factory:
        yield session_factory
    get_settings.cache_clear()


@pytest.fixture
def stubs(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Stubs:
    """Build the command's gateway from a stub provider instead of the real SDK client."""
    stubs = Stubs()

    def build(settings: LlmGatewaySettings, session_factory: sessionmaker[Session]) -> LlmGateway:
        return embedding_gateway(session_factory, stubs.embedder)

    monkeypatch.setattr(check_evidence_search, "build_openai_gateway", build)
    return stubs


def _store_corpus(factory: sessionmaker[Session], *, near_chunks: int) -> None:
    """Store document 9001 with `near_chunks` chunks close to any question, and 9002 far off."""
    with session_scope(factory) as session:
        for chunk_id in add_document(session, "9001", near_chunks):
            store_vector(session, chunk_id, [0.0], filler=0.5)
        for chunk_id in add_document(session, "9002", 1):
            store_vector(session, chunk_id, [1.0])


def test_prints_the_share_and_exits_0_when_every_question_finds_its_document(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    _store_corpus(factory, near_chunks=1)

    exit_code = main([str(FIXTURE)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines() == [
        "top-5 share: 2 of 2 questions (100.00%) found their document"
        f" (model {MODEL}, input version v1)",
        "  cms_ncd: 2 of 2 questions (100.00%)",
        "  spent $0.000008 in this run; $0.000008 of the $3.00 monthly budget is used",
    ]


def test_a_miss_is_listed_with_what_came_back_and_the_run_still_exits_0(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    # Five close chunks fill the top 5, so the far document 9002 is never returned.
    _store_corpus(factory, near_chunks=5)

    exit_code = main([str(FIXTURE)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines()[:3] == [
        "top-5 share: 1 of 2 questions (50.00%) found their document"
        f" (model {MODEL}, input version v1)",
        "  cms_ncd: 1 of 2 questions (50.00%)",
        "  missed question 2: expected cms_ncd 999.9002, found cms_ncd 999.9001",
    ]


def test_every_source_is_searched_at_once_and_the_share_is_split_by_source(
    factory: sessionmaker[Session],
    stubs: Stubs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A determination and a manual section share one section number and are equally close to
    # any question. The five chunks of manual section 9002 fill the rest of the top 5, so the
    # determination 9003, which points the other way, is never returned.
    with session_scope(factory) as session:
        for chunk_id in add_document(session, "9001", 1):
            store_vector(session, chunk_id, [0.0], filler=0.5)
        for chunk_id in add_document(session, "9001", 1, source=EvidenceSource.CMS_MANUAL):
            store_vector(session, chunk_id, [0.0], filler=0.5)
        for chunk_id in add_document(session, "9002", 5, source=EvidenceSource.CMS_MANUAL):
            store_vector(session, chunk_id, [1.0])
        for chunk_id in add_document(session, "9003", 1):
            store_vector(session, chunk_id, [-1.0])
    path = tmp_path / "questions.json"
    entries = [
        ("first?", "cms_ncd", "999.9001"),
        ("second?", "cms_manual", "999.9001"),
        ("third?", "cms_ncd", "999.9003"),
    ]
    path.write_text(
        json.dumps(
            [
                {"question": text, "expected_source": source, "expected_section_number": number}
                for text, source, number in entries
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main([str(path)])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines()[:4] == [
        "top-5 share: 2 of 3 questions (66.67%) found their document"
        f" (model {MODEL}, input version v1)",
        "  cms_ncd: 1 of 2 questions (50.00%)",
        "  cms_manual: 1 of 1 questions (100.00%)",
        "  missed question 3: expected cms_ncd 999.9003,"
        " found cms_ncd 999.9001, cms_manual 999.9001, cms_manual 999.9002",
    ]


def test_each_question_is_one_recorded_query_embedding_call(
    factory: sessionmaker[Session], stubs: Stubs
) -> None:
    _store_corpus(factory, near_chunks=1)

    main([str(FIXTURE)])

    assert [request.texts for request in stubs.embedder.requests] == [
        (QUESTION,),
        ("Is the other made-up service covered?",),
    ]
    assert len(embedding_calls(factory, QUERY_EMBEDDING_PURPOSE)) == 2


def test_no_stored_vector_exits_1_and_spends_nothing(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        add_document(session, "9001", 1)
        add_document(session, "9002", 1)

    exit_code = main([str(FIXTURE)])

    assert exit_code == 1
    assert stubs.embedder.requests == []
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [SPENT_NOTHING]
    assert captured.err == (
        f"the share cannot be measured: no vector is stored for model {MODEL}, input version"
        " v1: run pipelines.embed_evidence_corpus first\n"
    )


@pytest.mark.parametrize(
    ("embedder", "reason"),
    [
        (StubEmbedder(highest_cost="5.00"), "the monthly LLM budget is reached"),
        (
            StubEmbedder(errors={1: ProviderUnavailableError("timeout")}),
            "the embedding provider could not answer",
        ),
        (
            StubEmbedder(errors={1: ProviderRejectedError("BadRequestError (status 400)")}),
            "the provider refused the request: BadRequestError (status 400)",
        ),
        (
            StubEmbedder(dimensions=3),
            "an answer cannot be used (the call was paid for):"
            " the vector of the query text must be 1536 finite numbers",
        ),
        (
            StubEmbedder("another-model"),
            "an answer cannot be used (the call was paid for):"
            " the vector was made by another model than the one asked for",
        ),
    ],
    ids=["budget", "unavailable", "refused", "wrong size", "other model"],
)
def test_a_run_that_cannot_finish_exits_1_with_a_plain_message_and_no_share(
    factory: sessionmaker[Session],
    stubs: Stubs,
    capsys: pytest.CaptureFixture[str],
    embedder: StubEmbedder,
    reason: str,
) -> None:
    _store_corpus(factory, near_chunks=1)
    stubs.embedder = embedder

    exit_code = main([str(FIXTURE)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "share:" not in captured.out
    assert captured.out.startswith("  spent $")
    assert captured.err == f"the share cannot be measured: {reason}\n"
    assert "Traceback" not in captured.err


def test_an_expected_section_number_that_is_not_stored_exits_2_before_any_call(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        for chunk_id in add_document(session, "9001", 1):
            store_vector(session, chunk_id, [0.0], filler=0.5)

    exit_code = main([str(FIXTURE)])

    assert exit_code == 2
    assert stubs.embedder.requests == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "the question file expects section numbers no stored document has: cms_ncd 999.9002\n"
    )


def test_a_question_file_that_cannot_be_used_exits_2(
    factory: sessionmaker[Session],
    stubs: Stubs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps([]), encoding="utf-8")

    exit_code = main([str(path)])

    assert exit_code == 2
    assert stubs.embedder.requests == []
    assert capsys.readouterr().err == "the question file holds no question\n"


def test_a_missing_file_exits_2(
    factory: sessionmaker[Session], stubs: Stubs, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main([str(tmp_path / "missing.json")])

    assert raised.value.code == 2
    assert "file not found" in capsys.readouterr().err


def test_no_argument_exits_2(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main([])

    assert raised.value.code == 2
    assert stubs.embedder.requests == []


def test_the_output_and_the_log_hold_no_question_text(
    factory: sessionmaker[Session],
    stubs: Stubs,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _store_corpus(factory, near_chunks=5)

    main([str(FIXTURE)])

    captured = capsys.readouterr()
    for shown in (captured.out, captured.err, caplog.text):
        assert QUESTION not in shown
