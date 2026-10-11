"""CLI tests. The command really commits, so the made-up rows are removed afterwards.

The gateway is built from stub providers and the saved model is a stand-in: no real model is
called, no model file is loaded and no network is used.
"""

import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, update
from sqlalchemy.orm import Session, sessionmaker

from pipelines import run_agent
from pipelines.run_agent import main
from src.config.settings import LlmGatewaySettings, get_settings, load_llm_gateway_settings
from src.db.models import ClaimSampleLine, DocumentExtraction, EvidenceDocument, GeneratedDocument
from src.db.session import session_scope
from src.drafting.prompts import DRAFTING_PROMPT_VERSION, DRAFTING_PURPOSE, PROMPT_FILE_NAME
from src.llm.gateway import LlmGateway, ProviderRejectedError
from src.ml.training import WinModel
from tests.agent.helpers import agent_data, change_letter, fixed_model, stored_runs
from tests.drafting.helpers import (
    ABSENT_QUOTE,
    OPENING_TEXT,
    POLICY_TEXT,
    SYSTEM_TEXT,
    absent_quote_answer,
    drafting_gateway,
    good_answer,
    is_for,
    spent,
    stored_drafts,
    two_problem_answer,
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
# The made-up letter's deadline is 2008-09-16, so on the first day it passes the rules.
BEFORE_THE_DEADLINE = ["--rules-today", "2008-06-01"]
AFTER_THE_DEADLINE = ["--rules-today", "2009-01-01"]
VALIDATION = ["--split", "validation", "--prompt-version", PROMPT_VERSION, *BEFORE_THE_DEADLINE]
HEADLINE = "selected claims (validation split, agent v2, drafting prompt v999)"
SETTINGS_LINE = "  rules today 2008-06-01, amount floor $25, score threshold 0.30"


class Stubs:
    """The stand-ins the command works with; a test may swap them."""

    def __init__(self) -> None:
        self.primary = StubProvider(good_answer)
        self.embedder = StubEmbedder()
        self.model = fixed_model()


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
    monkeypatch.setenv("MODEL_DIR", str(tmp_path / "no-model-here"))
    monkeypatch.setenv("RULES_AMOUNT_FLOOR_USD", "25")
    monkeypatch.delenv("AGENT_WIN_THRESHOLD", raising=False)
    for name, value in LLM_ENV.items():
        monkeypatch.setenv(name, value)
    for name in LLM_FALLBACK_KEYS:
        monkeypatch.delenv(name, raising=False)
    # No `.env` file is read, so the test never sees a real key.
    monkeypatch.setattr(
        run_agent,
        "load_llm_gateway_settings",
        lambda: load_llm_gateway_settings(env_file=None),
    )
    get_settings.cache_clear()
    with agent_data(engine) as session_factory:
        yield session_factory
    get_settings.cache_clear()


@pytest.fixture
def stubs(factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Stubs:
    """Give the command stub providers and a stand-in model instead of the real ones."""
    stubs = Stubs()

    def build(settings: LlmGatewaySettings, session_factory: sessionmaker[Session]) -> LlmGateway:
        return drafting_gateway(session_factory, stubs.primary, embedder=stubs.embedder)

    def load(folder: Path) -> WinModel:
        return stubs.model

    monkeypatch.setattr(run_agent, "build_openai_gateway", build)
    monkeypatch.setattr(run_agent, "load_win_model", load)
    return stubs


def _without_time(output: str) -> str:
    return re.sub(r"time: \d+ s", "time: N s", output)


def _end_states(factory: sessionmaker[Session]) -> dict[str, str]:
    return {claim_id: row.end_state.value for claim_id, row in stored_runs(factory).items()}


def test_runs_one_split_stores_the_traces_and_prints_the_counts(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    change_letter(factory, CLAIM_2, appeal_deadline=None)

    exit_code = main(VALIDATION)

    assert exit_code == 0
    assert _end_states(factory) == {CLAIM_1: "needs_review", CLAIM_2: "needs_review_no_draft"}
    assert set(stored_drafts(factory)) == {CLAIM_1}
    captured = capsys.readouterr()
    assert captured.err == ""
    assert _without_time(captured.out).splitlines() == [
        f"ran the agent loop for 2 of 2 {HEADLINE}",
        SETTINGS_LINE,
        "  finished before this run: 0",
        "  needs_review: 1",
        "    drafted with at least one citation: 1",
        "  needs_review_no_draft: 1",
        "  stopped_by_rules: 0",
        "  below_threshold: 0",
        "  draft_failed: 0",
        "    blocked: 0",
        "    cut_off: 0",
        "    not_parseable: 0",
        "    no rules verdict: 0",
        "  answered by the fallback: 0",
        "  denied claims of the split left out (no current letter extraction): 0",
        "  time: N s",
        f"  spent ${spent(1)} in this run; ${spent(1)} of the $3.00 monthly budget is used",
    ]


def test_a_limit_takes_only_the_first_claims_of_the_split(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*VALIDATION, "--limit", "1"]) == 0

    assert set(stored_runs(factory)) == {CLAIM_1}
    assert capsys.readouterr().out.startswith("ran the agent loop for 1 of 1 selected claims")


def test_the_split_argument_chooses_the_claims(
    factory: sessionmaker[Session], stubs: Stubs
) -> None:
    assert main(["--split", "test", "--prompt-version", PROMPT_VERSION, *BEFORE_THE_DEADLINE]) == 0

    assert set(stored_runs(factory)) == {CLAIM_3}


def test_the_rules_date_decides_what_the_rules_stop_and_such_a_run_spends_nothing(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = ["--split", "validation", "--prompt-version", PROMPT_VERSION, *AFTER_THE_DEADLINE]

    assert main(arguments) == 0

    assert _end_states(factory) == {CLAIM_1: "stopped_by_rules", CLAIM_2: "stopped_by_rules"}
    assert (stubs.primary.requests, stubs.embedder.requests) == ([], [])
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "  rules today 2009-01-01, amount floor $25, score threshold 0.30"
    assert lines[6] == "  stopped_by_rules: 2"
    # No call was ever recorded, so the sum has no decimal places.
    assert lines[-1] == "  spent $0 in this run; $0 of the $3.00 monthly budget is used"


def test_the_threshold_and_the_floor_come_from_the_settings(
    factory: sessionmaker[Session],
    stubs: Stubs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_WIN_THRESHOLD", "0.9")
    monkeypatch.setenv("RULES_AMOUNT_FLOOR_USD", "10.50")
    get_settings.cache_clear()

    assert main(VALIDATION) == 0

    # The stand-in model gives every claim 0.75.
    assert _end_states(factory) == {CLAIM_1: "below_threshold", CLAIM_2: "below_threshold"}
    assert stubs.primary.requests == []
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "  rules today 2008-06-01, amount floor $10.50, score threshold 0.90"
    assert lines[7] == "  below_threshold: 2"


def test_a_threshold_outside_0_to_1_fails_at_startup_and_names_the_key(
    factory: sessionmaker[Session], stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_WIN_THRESHOLD", "1.5")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="agent_win_threshold"):
        main(VALIDATION)

    assert stubs.primary.requests == []
    assert stored_runs(factory) == {}


def test_a_second_run_makes_no_call_and_spends_nothing(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    main(VALIDATION)
    capsys.readouterr()
    stubs.primary = StubProvider(good_answer)
    stubs.embedder = StubEmbedder()

    assert main(VALIDATION) == 0

    assert (stubs.primary.requests, stubs.embedder.requests) == ([], [])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"ran the agent loop for 0 of 2 {HEADLINE}"
    assert lines[2] == "  finished before this run: 2"
    assert lines[-1] == (
        f"  spent $0.000000 in this run; ${spent(2)} of the $3.00 monthly budget is used"
    )


def test_failed_drafts_are_counted_the_run_exits_0_and_the_next_run_takes_them_again(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    stubs.primary = StubProvider(
        lambda request: two_problem_answer(request) if is_for(CLAIM_1)(request) else "not JSON"
    )

    assert main(VALIDATION) == 0

    assert _end_states(factory) == {CLAIM_1: "draft_failed", CLAIM_2: "draft_failed"}
    assert stored_drafts(factory) == {}
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines()[8:13] == [
        "  draft_failed: 2",
        "    blocked: 1",
        "    cut_off: 0",
        "    not_parseable: 1",
        "    no rules verdict: 0",
    ]

    stubs.primary = StubProvider(good_answer)
    assert main(VALIDATION) == 0

    assert _end_states(factory) == {CLAIM_1: "needs_review", CLAIM_2: "needs_review"}
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"ran the agent loop for 2 of 2 {HEADLINE}"
    assert lines[2:4] == ["  finished before this run: 0", "  needs_review: 2"]


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

    assert set(stored_runs(factory)) == {CLAIM_2}
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"ran the agent loop for 1 of 1 {HEADLINE}"
    assert lines[14] == "  denied claims of the split left out (no current letter extraction): 1"


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
    assert _end_states(factory) == {CLAIM_1: "needs_review"}  # what was finished is kept
    captured = capsys.readouterr()
    assert captured.out.startswith("ran the agent loop for 1 of 2 selected claims")
    assert captured.err == (
        "stopped early: a provider refused the request: BadRequestError (status 400);"
        " 1 claims were not tried. Run the same command again to continue.\n"
    )


def test_stops_cleanly_when_the_budget_is_reached_and_exits_1(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    stubs.primary = StubProvider(good_answer, highest_cost="5.00")

    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert stubs.primary.requests == []
    assert stored_runs(factory) == {}
    captured = capsys.readouterr()
    assert captured.err == (
        "stopped early: the monthly LLM budget is reached; 2 claims were not tried."
        " Run the same command again to continue.\n"
    )
    assert "Traceback" not in captured.err


def test_evidence_without_vectors_exits_1_before_any_call(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        # The chunks and their vectors go with the document, as in a new corpus load.
        for document in session.query(EvidenceDocument):
            session.delete(document)

    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert (stubs.primary.requests, stubs.embedder.requests) == ([], [])
    assert stored_runs(factory) == {}
    captured = capsys.readouterr()
    assert "stopped early: the evidence cannot be searched: no vector is stored" in captured.err
    assert "run pipelines.embed_evidence_corpus first; 2 claims were not tried." in captured.err


def test_a_missing_saved_model_exits_1_before_any_claim_is_read(
    factory: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    # Without the `stubs` fixture: the real model loader, pointed at a folder with no model.
    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert stored_runs(factory) == {}
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.endswith(": run pipelines.train_win_model first\n")


def test_a_model_folder_that_is_a_file_exits_1_with_a_plain_message(
    factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    not_a_folder = tmp_path / "a-file"
    not_a_folder.write_text("made up", encoding="utf-8")
    monkeypatch.setenv("MODEL_DIR", str(not_a_folder))
    get_settings.cache_clear()

    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert stored_runs(factory) == {}
    captured = capsys.readouterr()
    assert captured.err == "the MODEL_DIR setting names a file, not a folder\n"


def test_a_split_with_no_claim_to_take_exits_1_before_a_gateway_is_built(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--split", "train", "--prompt-version", PROMPT_VERSION, *BEFORE_THE_DEADLINE])

    assert exit_code == 1
    assert (stubs.primary.requests, stubs.embedder.requests) == ([], [])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "the train split has no denied claim with a current letter extraction" in captured.err


def test_a_label_that_no_longer_fits_its_lines_exits_1_before_any_call(
    factory: sessionmaker[Session], stubs: Stubs, capsys: pytest.CaptureFixture[str]
) -> None:
    with session_scope(factory) as session:
        session.execute(delete(ClaimSampleLine).where(ClaimSampleLine.source_claim_id == CLAIM_2))

    exit_code = main(VALIDATION)

    assert exit_code == 1
    assert (stubs.primary.requests, stubs.embedder.requests) == ([], [])
    assert stored_runs(factory) == {}
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "cannot read the claims to take: the stored label of 1 of the 2 selected claims no"
        " longer fits its stored lines: run pipelines.run_data_pipeline again\n"
    )


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
    assert captured.err.startswith("cannot read the claims to take: a stored letter extraction")
    assert PATIENT_NAME not in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--split", "validation"],
        ["--rules-today", "2008-06-01"],
        ["--split", "holdout", "--rules-today", "2008-06-01"],
        ["--split", "validation", "--rules-today", "June 2008"],
        ["--split", "validation", "--rules-today", "2008-06-31"],
        ["--split", "validation", "--rules-today", "2008-06-01", "--limit", "0"],
        ["--split", "validation", "--rules-today", "2008-06-01", "--limit", "many"],
        ["--split", "validation", "--rules-today", "2008-06-01", "--prompt-version", "../v1"],
        ["--split", "validation", "--rules-today", "2008-06-01", "--prompt-version", "v998"],
    ],
)
def test_bad_arguments_exit_2_and_make_no_call(
    factory: sessionmaker[Session], stubs: Stubs, arguments: list[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(arguments)

    assert raised.value.code == 2
    assert (stubs.primary.requests, stubs.embedder.requests) == ([], [])
    assert stored_runs(factory) == {}


def test_a_missing_gateway_setting_fails_before_any_claim_is_read_and_names_the_key(
    factory: sessionmaker[Session], stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_PRIMARY_API_KEY")

    with pytest.raises(ValueError, match="llm_primary_api_key"):
        main(VALIDATION)

    assert stubs.primary.requests == []


def test_the_default_prompt_version_is_the_one_in_the_code(
    factory: sessionmaker[Session], stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real prompt folder: its file exists for the default version.
    monkeypatch.delenv("PROMPT_DIR")
    get_settings.cache_clear()
    stubs.primary = StubProvider(good_answer, highest_cost="5.00")  # over the cap: no letter

    assert main(["--split", "validation", *BEFORE_THE_DEADLINE]) == 1

    assert DRAFTING_PROMPT_VERSION == "v1"
    assert stored_drafts(factory) == {}
    assert stored_runs(factory) == {}


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
