"""CLI tests. The command really commits, so the made-up rows are removed afterwards.

The gateway is built from a stub embedding provider: no real model is called and no network
is used.
"""

import logging
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from pipelines import embed_evidence_corpus
from pipelines.embed_evidence_corpus import main
from src.config.settings import LlmGatewaySettings, get_settings, load_llm_gateway_settings
from src.db.session import session_scope
from src.llm.gateway import LlmGateway, ProviderRejectedError, ProviderUnavailableError
from tests.rag.helpers import (
    CHUNK_TEXT,
    DOCUMENT_TITLE,
    MODEL,
    StubEmbedder,
    add_document,
    embedding_gateway,
    evidence_tables,
    stored_vectors,
)

LLM_ENV = {
    "LLM_MONTHLY_BUDGET_USD": "3.00",
    "LLM_TIMEOUT_SECONDS": "30",
    "LLM_PRIMARY_BASE_URL": "https://llm.example.com/v1",
    "LLM_PRIMARY_MODEL": "example-small-model",
    "LLM_PRIMARY_API_KEY": "made-up-key",
    "LLM_PRIMARY_INPUT_USD_PER_MTOK": "1.00",
    "LLM_PRIMARY_OUTPUT_USD_PER_MTOK": "5.00",
    "LLM_EMBEDDING_MODEL": MODEL,
    "LLM_EMBEDDING_USD_PER_MTOK": "0.02",
}
LLM_FALLBACK_KEYS = (
    "LLM_FALLBACK_BASE_URL",
    "LLM_FALLBACK_MODEL",
    "LLM_FALLBACK_API_KEY",
    "LLM_FALLBACK_INPUT_USD_PER_MTOK",
    "LLM_FALLBACK_OUTPUT_USD_PER_MTOK",
)


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
        embed_evidence_corpus,
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

    monkeypatch.setattr(embed_evidence_corpus, "build_openai_gateway", build)
    return stubs


def _store_chunks(factory: sessionmaker[Session], chunk_count: int) -> None:
    with session_scope(factory) as session:
        add_document(session, "9001", chunk_count)


def _without_time(output: str) -> str:
    return re.sub(r"time: \d+ s", "time: N s", output)


def test_embeds_the_stored_chunks_and_prints_the_counts(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    _store_chunks(factory, 150)

    exit_code = main([])

    assert exit_code == 0
    assert len(stored_vectors(factory)) == 150
    captured = capsys.readouterr()
    assert captured.err == ""
    assert _without_time(captured.out).splitlines() == [
        f"embedded 150 of 150 chunks (model {MODEL}, input version v1)",
        "  already embedded before this run: 0",
        "  time: N s",
        "  spent $0.000008 in this run; $0.000008 of the $3.00 monthly budget is used",
    ]


def test_a_second_run_embeds_nothing_and_spends_nothing(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    _store_chunks(factory, 3)
    main([])
    capsys.readouterr()
    stubs.embedder = StubEmbedder()

    exit_code = main([])

    assert exit_code == 0
    assert stubs.embedder.requests == []
    captured = capsys.readouterr()
    assert captured.err == ""
    assert _without_time(captured.out).splitlines() == [
        f"embedded 0 of 3 chunks (model {MODEL}, input version v1)",
        "  already embedded before this run: 3",
        "  time: N s",
        "  spent $0.000000 in this run; $0.000004 of the $3.00 monthly budget is used",
    ]


@pytest.mark.parametrize(
    ("embedder", "reason"),
    [
        (StubEmbedder(highest_cost="5.00"), "the monthly LLM budget is reached"),
        (
            StubEmbedder(errors={2: ProviderUnavailableError("timeout")}),
            "the embedding provider could not answer",
        ),
        (
            StubEmbedder(errors={2: ProviderRejectedError("BadRequestError (status 400)")}),
            "the provider refused the request: BadRequestError (status 400)",
        ),
        (
            StubEmbedder(dimensions=3),
            "the answer cannot be stored (the call was paid for):"
            " a vector is not 1536 finite numbers",
        ),
    ],
    ids=["budget", "unavailable", "refused", "wrong size"],
)
def test_a_run_that_stops_early_exits_1_with_a_plain_message(
    factory: sessionmaker[Session],
    stubs: Stubs,
    capsys: pytest.CaptureFixture[str],
    embedder: StubEmbedder,
    reason: str,
) -> None:
    _store_chunks(factory, 150)
    stubs.embedder = embedder

    exit_code = main([])

    assert exit_code == 1
    stored = len(stored_vectors(factory))
    captured = capsys.readouterr()
    assert captured.out.startswith(f"embedded {stored} of 150 chunks")
    assert captured.err == (
        f"stopped early: {reason}; {150 - stored} chunks have no vector."
        " Run the same command again to continue.\n"
    )
    assert "Traceback" not in captured.err


def test_a_failed_second_batch_keeps_the_first(
    factory: sessionmaker[Session], stubs: Stubs
) -> None:
    _store_chunks(factory, 150)
    stubs.embedder = StubEmbedder(errors={2: ProviderUnavailableError("timeout")})

    assert main([]) == 1
    assert len(stored_vectors(factory)) == 100


def test_no_stored_chunk_exits_1_and_names_the_command_to_run_first(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([])

    assert exit_code == 1
    assert stubs.embedder.requests == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "no evidence chunk is stored: run pipelines.load_evidence_corpus first\n"
    )


def test_an_unknown_argument_exits_2(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--limit", "5"])

    assert raised.value.code == 2
    assert stubs.embedder.requests == []
    assert "unrecognized arguments" in capsys.readouterr().err


def test_the_output_and_the_log_hold_no_title_and_no_chunk_text(
    factory: sessionmaker[Session],
    stubs: Stubs,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _store_chunks(factory, 3)

    main([])

    captured = capsys.readouterr()
    for shown in (captured.out, captured.err, caplog.text):
        assert CHUNK_TEXT not in shown
        assert DOCUMENT_TITLE not in shown
