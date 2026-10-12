"""CLI tests. The command really commits, so the made-up rows are removed afterwards.

The gateway is built from stub providers: no real model is called and no network is used.
"""

import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session, sessionmaker

from pipelines import draft_appeals
from pipelines.draft_appeals import main
from src.config.settings import LlmGatewaySettings, get_settings, load_llm_gateway_settings
from src.db.models import DocumentExtraction, EvidenceDocument, GeneratedDocument
from src.db.session import session_scope
from src.drafting.prompts import DRAFTING_PROMPT_VERSION, DRAFTING_PURPOSE, PROMPT_FILE_NAME
from src.llm.gateway import LlmGateway, ProviderRejectedError
from tests.drafting.helpers import (
    ABSENT_QUOTE,
    OPENING_TEXT,
    POLICY_TEXT,
    SYSTEM_TEXT,
    absent_quote_answer,
    drafting_data,
    drafting_gateway,
    good_answer,
    is_for,
    placeholder_answer,
    spent,
    stored_drafts,
    two_problem_answer,
    uncited_answer,
)
from tests.extraction.helpers import (
    CLAIM_1,
    CLAIM_2,
    CLAIM_3,
    LETTER,
    PATIENT_NAME,
    PROMPT_VERSION,
    StubProvider,
)
from tests.rag.helpers import CHUNK_TEXT, MODEL, StubEmbedder

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
VALIDATION = ["--split", "validation", "--prompt-version", PROMPT_VERSION]
HEADLINE = "selected claims (validation split, prompt v999, search question v3)"


class Stubs:
    """The stub providers the command's gateway is built from; a test may swap them."""

    def __init__(self) -> None:
        self.primary = StubProvider(good_answer)
        self.embedder = StubEmbedder()


@pytest.fixture
def factory(
    engine: Engine, test_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    """Point the command at the test database and at a made-up prompt file."""
    path = tmp_path / DRAFTING_PURPOSE / PROMPT_VERSION / PROMPT_FILE_NAME
    path.parent.mkdir(parents=True)
    path.write_text(SYSTEM_TEXT, encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("PROMPT_DIR", str(tmp_path))
    for name, value in LLM_ENV.items():
        monkeypatch.setenv(name, value)
    for name in LLM_FALLBACK_KEYS:
        monkeypatch.delenv(name, raising=False)
    # No `.env` file is read, so the test never sees a real key.
    monkeypatch.setattr(
        draft_appeals,
        "load_llm_gateway_settings",
        lambda: load_llm_gateway_settings(env_file=None),
    )
    get_settings.cache_clear()
    with drafting_data(engine) as session_factory:
        yield session_factory
    get_settings.cache_clear()


@pytest.fixture
def stubs(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Stubs:
    """Build the command's gateway from stub providers instead of the real SDK clients."""
    stubs = Stubs()

    def build(settings: LlmGatewaySettings, session_factory: sessionmaker[Session]) -> LlmGateway:
        return drafting_gateway(session_factory, stubs.primary, embedder=stubs.embedder)

    monkeypatch.setattr(draft_appeals, "build_openai_gateway", build)
    return stubs


def _without_time(output: str) -> str:
    return re.sub(r"time: \d+ s", "time: N s", output)


def test_drafts_one_split_stores_the_letters_and_prints_the_counts(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(VALIDATION)

    assert exit_code == 0
    assert set(stored_drafts(factory)) == {CLAIM_1, CLAIM_2}
    captured = capsys.readouterr()
    assert captured.err == ""
    assert _without_time(captured.out).splitlines() == [
        f"drafted 2 of 2 {HEADLINE}",
        "  already drafted before this run: 0",
        "  drafted with at least one citation: 2",
        "  blocked by the citation validator: 0",
        "    policy_without_citation: 0",
        "    unknown_label: 0",
        "    quote_too_short: 0",
        "    quote_not_in_evidence: 0",
        "  failed to parse: 0",
        "  stopped for length: 0",
        "  refused for placeholder text: 0",
        "  answered by the fallback: 0",
        "  denied claims of the split left out (no current letter extraction): 0",
        "  time: N s",
        f"  spent ${spent(2)} in this run; ${spent(2)} of the $3.00 monthly budget is used",
    ]


def test_a_limit_drafts_only_the_first_claims_of_the_split(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*VALIDATION, "--limit", "1"]) == 0

    assert set(stored_drafts(factory)) == {CLAIM_1}
    assert capsys.readouterr().out.startswith("drafted 1 of 1 selected claims")


def test_the_split_argument_chooses_the_claims(
    factory: sessionmaker[Session], stubs: Stubs
) -> None:
    assert main(["--split", "test", "--prompt-version", PROMPT_VERSION]) == 0

    assert set(stored_drafts(factory)) == {CLAIM_3}


def test_a_second_run_makes_no_call_and_spends_nothing(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    main(VALIDATION)
    capsys.readouterr()
    stubs.primary = StubProvider(good_answer)
    stubs.embedder = StubEmbedder()

    assert main(VALIDATION) == 0

    assert stubs.primary.requests == []

    assert stubs.embedder.requests == []
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"drafted 0 of 2 {HEADLINE}"
    assert lines[1] == "  already drafted before this run: 2"
    assert lines[-1] == (
        f"  spent $0.000000 in this run; ${spent(2)} of the $3.00 monthly budget is used"
    )


def test_blocked_and_unusable_drafts_are_counted_and_the_run_still_exits_0(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    stubs.primary = StubProvider(
        lambda request: two_problem_answer(request) if is_for(CLAIM_1)(request) else "not JSON"
    )

    assert main(VALIDATION) == 0

    assert stored_drafts(factory) == {}
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert lines[0] == f"drafted 0 of 2 {HEADLINE}"
    assert lines[3:9] == [
        "  blocked by the citation validator: 1",
        "    policy_without_citation: 1",
        "    unknown_label: 0",
        "    quote_too_short: 0",
        "    quote_not_in_evidence: 1",
        "  failed to parse: 1",
    ]


def test_a_cut_off_answer_and_an_uncited_letter_are_counted(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    stubs.primary = StubProvider(
        uncited_answer,
        stop_reason=lambda request: "length" if is_for(CLAIM_1)(request) else "stop",
    )

    assert main(VALIDATION) == 0

    assert set(stored_drafts(factory)) == {CLAIM_2}
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"drafted 1 of 2 {HEADLINE}"
    assert lines[2] == "  drafted with at least one citation: 0"
    assert lines[9] == "  stopped for length: 1"


def test_a_draft_with_placeholder_text_is_counted_and_its_text_is_not_printed(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    stubs.primary = StubProvider(
        lambda request: (
            placeholder_answer(request) if is_for(CLAIM_1)(request) else good_answer(request)
        )
    )

    assert main(VALIDATION) == 0

    assert set(stored_drafts(factory)) == {CLAIM_2}
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0] == f"drafted 1 of 2 {HEADLINE}"
    assert lines[8:11] == [
        "  failed to parse: 0",
        "  stopped for length: 0",
        "  refused for placeholder text: 1",
    ]
    assert "Your Name" not in captured.out + captured.err


def test_a_claim_without_a_current_letter_extraction_is_left_out_and_counted(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(GeneratedDocument)
            .where(
                GeneratedDocument.source_claim_id == CLAIM_1,
                GeneratedDocument.document_type == LETTER,
            )
            .values(text="A newer made-up letter.")
        )

    assert main(VALIDATION) == 0

    assert set(stored_drafts(factory)) == {CLAIM_2}
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"drafted 1 of 1 {HEADLINE}"
    assert lines[12] == "  denied claims of the split left out (no current letter extraction): 1"


def test_stops_cleanly_when_the_budget_is_reached_and_exits_1(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    main([*VALIDATION, "--limit", "1"])
    capsys.readouterr()
    stubs.primary = StubProvider(good_answer, highest_cost="5.00")

    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert stubs.primary.requests == []
    assert set(stored_drafts(factory)) == {CLAIM_1}  # what the first run finished is kept
    captured = capsys.readouterr()
    assert captured.out.startswith("drafted 0 of 2 selected claims")
    assert "  already drafted before this run: 1\n" in captured.out
    assert captured.err == (
        "stopped early: the monthly LLM budget is reached; 1 claims were not tried."
        " Run the same command again to continue.\n"
    )


def test_stops_cleanly_when_a_provider_refuses_the_request_and_exits_1(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    stubs.primary = StubProvider(
        good_answer,
        error=lambda request: (
            None
            if is_for(CLAIM_1)(request)
            else ProviderRejectedError("BadRequestError (status 400)")
        ),
    )

    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert set(stored_drafts(factory)) == {CLAIM_1}  # what was finished is kept
    captured = capsys.readouterr()
    assert captured.out.startswith("drafted 1 of 2 selected claims")
    assert captured.err == (
        "stopped early: a provider refused the request: BadRequestError (status 400);"
        " 1 claims were not tried. Run the same command again to continue.\n"
    )


def test_evidence_without_vectors_exits_1_before_any_call(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        # The chunks and their vectors go with the document, as in a new corpus load.
        for document in session.query(EvidenceDocument):
            session.delete(document)

    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert stubs.primary.requests == []
    assert stubs.embedder.requests == []
    assert stored_drafts(factory) == {}
    captured = capsys.readouterr()
    assert "stopped early: the evidence cannot be searched: no vector is stored" in captured.err
    assert "run pipelines.embed_evidence_corpus first; 2 claims were not tried." in captured.err
    assert "Traceback" not in captured.err


def test_a_split_with_no_claim_to_draft_exits_1_before_a_gateway_is_built(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--split", "train", "--prompt-version", PROMPT_VERSION])

    assert exit_code == 1
    assert stubs.primary.requests == []
    assert stubs.embedder.requests == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "the train split has no denied claim with a current letter extraction" in captured.err


def test_a_stored_extraction_that_no_longer_fits_exits_1_with_a_plain_message(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(DocumentExtraction)
            .where(DocumentExtraction.source_claim_id == CLAIM_2)
            .values(fields={"patient_name": {"value": PATIENT_NAME, "confidence": 7}})
        )

    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert stubs.primary.requests == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("cannot read the claims to draft: a stored letter extraction")
    assert PATIENT_NAME not in captured.err


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
    assert stubs.embedder.requests == []
    assert stored_drafts(factory) == {}


def test_a_missing_gateway_setting_fails_before_any_claim_is_read_and_names_the_key(
    factory: sessionmaker[Session], stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_PRIMARY_API_KEY")

    with pytest.raises(ValueError, match="llm_primary_api_key"):
        main(VALIDATION)

    assert stubs.primary.requests == []


def test_the_default_prompt_version_is_the_one_in_the_code(
    factory: sessionmaker[Session],
    stubs: Stubs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The real prompt folder: its file exists for the default version.
    monkeypatch.delenv("PROMPT_DIR")
    get_settings.cache_clear()
    stubs.primary = StubProvider(good_answer, highest_cost="5.00")  # over the cap: no letter

    assert main(["--split", "validation"]) == 1

    assert DRAFTING_PROMPT_VERSION == "v2"
    headline = capsys.readouterr().out.splitlines()[0]
    assert f"prompt {DRAFTING_PROMPT_VERSION}, " in headline
    assert stored_drafts(factory) == {}


def test_no_letter_text_and_no_evidence_text_is_printed_or_logged(
    factory: sessionmaker[Session],
    stubs: Stubs,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    stubs.primary = StubProvider(
        lambda request: (
            absent_quote_answer(request) if is_for(CLAIM_1)(request) else good_answer(request)
        )
    )

    main(VALIDATION)

    captured = capsys.readouterr()
    for shown in (captured.out, captured.err, caplog.text):
        for hidden in (
            PATIENT_NAME,
            "MBR-000001",
            CHUNK_TEXT,
            ABSENT_QUOTE,
            OPENING_TEXT,
            POLICY_TEXT,
            CLAIM_1,
        ):
            assert hidden not in shown
