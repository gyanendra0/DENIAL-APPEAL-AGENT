"""CLI tests. The command really commits, so the made-up rows are removed afterwards.

The gateway is built from stub providers: no real model is called and no network is used.
"""

import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from pipelines import extract_documents
from pipelines.extract_documents import main
from src.config.settings import LlmGatewaySettings, get_settings, load_llm_gateway_settings
from src.db.models import DocumentType
from src.db.session import session_scope
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.extraction.store import read_extraction_rows
from src.llm.gateway import LlmGateway
from tests.extraction.helpers import (
    CLAIM_1,
    CLAIM_3,
    LETTER,
    NOTE,
    PATIENT_NAME,
    PROMPT_VERSION,
    VALIDATION_KEYS,
    StubProvider,
    gateway,
    is_about,
    right_answer,
    stored_documents,
    system_text,
)

LLM_ENV = {
    "LLM_MONTHLY_BUDGET_USD": "3.00",
    "LLM_TIMEOUT_SECONDS": "30",
    "LLM_PRIMARY_BASE_URL": "https://llm.example.com/v1",
    "LLM_PRIMARY_MODEL": "example-small-model",
    "LLM_PRIMARY_API_KEY": "made-up-key",
    "LLM_PRIMARY_INPUT_USD_PER_MTOK": "1.00",
    "LLM_PRIMARY_OUTPUT_USD_PER_MTOK": "5.00",
}
LLM_FALLBACK_KEYS = (
    "LLM_FALLBACK_BASE_URL",
    "LLM_FALLBACK_MODEL",
    "LLM_FALLBACK_API_KEY",
    "LLM_FALLBACK_INPUT_USD_PER_MTOK",
    "LLM_FALLBACK_OUTPUT_USD_PER_MTOK",
)
VALIDATION = ["--split", "validation", "--prompt-version", PROMPT_VERSION]


class Stubs:
    """The stub provider the command's gateway is built from; a test may swap it."""

    def __init__(self) -> None:
        self.primary = StubProvider()


@pytest.fixture
def factory(
    engine: Engine, test_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    """Point the command at the test database and at made-up prompt files."""
    for kind in DocumentType:
        path = tmp_path / "extraction" / PROMPT_VERSION / f"{kind.value}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(system_text(kind), encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("PROMPT_DIR", str(tmp_path))
    for name, value in LLM_ENV.items():
        monkeypatch.setenv(name, value)
    for name in LLM_FALLBACK_KEYS:
        monkeypatch.delenv(name, raising=False)
    # No `.env` file is read, so the test never sees a real key.
    monkeypatch.setattr(
        extract_documents,
        "load_llm_gateway_settings",
        lambda: load_llm_gateway_settings(env_file=None),
    )
    get_settings.cache_clear()
    with stored_documents(engine) as session_factory:
        yield session_factory
    get_settings.cache_clear()


@pytest.fixture
def stubs(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Stubs:
    """Build the command's gateway from a stub provider instead of the real SDK clients."""
    stubs = Stubs()

    def build(settings: LlmGatewaySettings, session_factory: sessionmaker[Session]) -> LlmGateway:
        return gateway(session_factory, stubs.primary)

    monkeypatch.setattr(extract_documents, "build_openai_gateway", build)
    return stubs


def _stored_keys(factory: sessionmaker[Session]) -> set[tuple[str, DocumentType]]:
    with session_scope(factory) as session:
        return set(read_extraction_rows(session, PROMPT_VERSION))


def _without_time(output: str) -> str:
    return re.sub(r"time: \d+ s", "time: N s", output)


def test_extracts_one_split_stores_the_results_and_prints_the_counts(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(VALIDATION)

    assert exit_code == 0
    assert _stored_keys(factory) == set(VALIDATION_KEYS)
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = _without_time(captured.out).splitlines()
    assert lines[:8] == [
        "extracted 5 of 5 selected documents (validation split, prompt v999)",
        "  already extracted before this run: 0",
        "  failed to parse: 0",
        "  stopped for length: 0",
        "  answered by the fallback: 0",
        "  time: N s",
        "  spent $0.000615 in this run; $0.000615 of the $3.00 monthly budget is used",
        "exact match with the answer keys (5 documents with a result):",
    ]
    assert "  denial_letter (2 documents): 30 of 30 values" in lines
    assert "  clinical_note (2 documents): 16 of 16 values" in lines
    assert "  prior_auth (1 documents): 12 of 12 values" in lines


def test_a_limit_extracts_only_the_first_documents_of_the_split(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*VALIDATION, "--limit", "2"]) == 0

    assert _stored_keys(factory) == {(CLAIM_1, LETTER), (CLAIM_1, NOTE)}
    assert capsys.readouterr().out.startswith("extracted 2 of 2 selected documents")


def test_the_split_argument_chooses_the_documents(
    factory: sessionmaker[Session], stubs: Stubs
) -> None:
    assert main(["--split", "test", "--prompt-version", PROMPT_VERSION]) == 0

    assert _stored_keys(factory) == {(CLAIM_3, LETTER), (CLAIM_3, NOTE)}


def test_a_second_run_makes_no_model_call_and_still_prints_the_match_count(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    main(VALIDATION)
    capsys.readouterr()
    stubs.primary = StubProvider()

    assert main(VALIDATION) == 0

    assert stubs.primary.requests == []
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "extracted 0 of 5 selected documents (validation split, prompt v999)"
    assert lines[1] == "  already extracted before this run: 5"
    assert (
        lines[6] == "  spent $0.000000 in this run; $0.000615 of the $3.00 monthly budget is used"
    )
    assert lines[7] == "exact match with the answer keys (5 documents with a result):"


def test_failed_extractions_are_counted_and_the_run_still_exits_0(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    # Each made-up text names its document type.
    stubs.primary = StubProvider(
        lambda request: "not JSON" if "denial_letter" in request.user else right_answer(request),
        stop_reason=lambda request: "length" if "prior_auth" in request.user else "stop",
    )

    assert main(VALIDATION) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "extracted 2 of 5 selected documents (validation split, prompt v999)"
    assert lines[2] == "  failed to parse: 2"
    assert lines[3] == "  stopped for length: 1"
    assert lines[7] == "exact match with the answer keys (2 documents with a result):"


def test_stops_cleanly_when_the_budget_is_reached_and_exits_1(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    main([*VALIDATION, "--limit", "3"])
    capsys.readouterr()
    stubs.primary = StubProvider(highest_cost="5.00")

    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert stubs.primary.requests == []
    assert len(_stored_keys(factory)) == 3  # what the first run finished is kept
    captured = capsys.readouterr()
    assert captured.out.startswith("extracted 0 of 5 selected documents")
    assert "  already extracted before this run: 3\n" in captured.out
    assert captured.err == (
        "stopped early: the monthly LLM budget is reached; 2 documents were not tried."
        " Run the same command again to continue.\n"
    )


def test_a_split_with_no_document_exits_1_before_a_gateway_is_built(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--split", "train", "--prompt-version", PROMPT_VERSION])

    assert exit_code == 1
    assert stubs.primary.requests == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "the train split has no document" in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--split", "holdout"],
        ["--split", "validation", "--limit", "0"],
        ["--split", "validation", "--limit", "many"],
        ["--split", "validation", "--prompt-version", "../v1"],
        ["--split", "validation", "--prompt-version", "v998"],
    ],
)
def test_bad_arguments_exit_2_and_make_no_call(
    factory: sessionmaker[Session], stubs: Stubs, arguments: list[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(arguments)

    assert raised.value.code == 2
    assert stubs.primary.requests == []
    assert _stored_keys(factory) == set()


def test_a_missing_gateway_setting_fails_before_any_document_is_read_and_names_the_key(
    factory: sessionmaker[Session], stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_PRIMARY_API_KEY")

    with pytest.raises(ValueError, match="llm_primary_api_key"):
        main(VALIDATION)

    assert stubs.primary.requests == []


def test_the_default_prompt_version_is_the_one_in_the_code(
    factory: sessionmaker[Session], stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real prompt folder: its files exist for the default version.
    monkeypatch.delenv("PROMPT_DIR")
    get_settings.cache_clear()
    stubs.primary = StubProvider(highest_cost="5.00")  # over the cap: nothing is called or stored

    assert main(["--split", "validation"]) == 1

    assert EXTRACTION_PROMPT_VERSION == "v1"


def test_no_document_text_and_no_value_is_printed_or_logged(
    factory: sessionmaker[Session],
    stubs: Stubs,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    stubs.primary = StubProvider(
        lambda request: "not JSON" if is_about(CLAIM_1)(request) else right_answer(request)
    )

    main(VALIDATION)

    captured = capsys.readouterr()
    for shown in (captured.out, captured.err, caplog.text):
        assert PATIENT_NAME not in shown
        assert "Made-up" not in shown
        assert "MBR-000001" not in shown
