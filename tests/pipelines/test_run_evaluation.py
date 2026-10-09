"""CLI tests. The command reads committed rows, so each test cleans the tables afterwards.

The claims are the made-up ones of the win-model tests (tests/ml/made_up_claims.py), with a
tiny model trained on them into a temporary folder. Four denied claims of the test split get
a made-up letter and note each; a test stores the extraction results it needs. No real model
is called and no stored model file is loaded.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session

from pipelines.run_evaluation import main
from src.config.settings import get_settings
from src.db.base import Base
from src.db.models import (
    ClaimSample,
    ClaimSampleLabel,
    ClaimSampleLine,
    DatasetSplit,
    DocumentExtraction,
    GeneratedDocument,
    LlmCall,
)
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.extraction.store import save_extraction_row
from src.llm.gateway import LlmGateway
from src.ml.training import FACTS_FILE_NAME, MODEL_FILE_NAME, save_win_model
from src.ml.win_model_run import load_training_data, train_and_score
from tests.conftest import ROOT
from tests.evaluation.helpers import result_row, stored_document
from tests.extraction.helpers import (
    ANSWER_KEYS,
    LETTER,
    NOTE,
    PATIENT_NAME,
    PROMPT_VERSION,
    document_text,
)
from tests.ml.made_up_claims import DENIED_COUNT, store_made_up_claims

TABLES: tuple[type[Base], ...] = (
    ClaimSample,
    ClaimSampleLine,
    ClaimSampleLabel,
    GeneratedDocument,
    DocumentExtraction,
)
COUNTED_TABLES: tuple[type[Base], ...] = (*TABLES, LlmCall)
DOCUMENT_CLAIMS = 4
# The made-up letter's deadline is 2008-09-16, so on this day its answer key passes the rules.
ARGS = ["--rules-today", "2008-06-01", "--prompt-version", PROMPT_VERSION]
MET = "Stage 3 targets met: yes"
NOT_MET = "Stage 3 targets met: no"


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    return tmp_path / "trained"


@pytest.fixture
def database(
    engine: Engine, test_database_url: str, model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Point the command at the test database and a temporary model folder."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("MODEL_DIR", str(model_dir))
    monkeypatch.setenv("RULES_AMOUNT_FLOOR_USD", "25")
    get_settings.cache_clear()
    _empty_tables(engine)  # in case an earlier run was killed before its clean-up
    yield engine
    get_settings.cache_clear()
    _empty_tables(engine)


@pytest.fixture
def claims(database: Engine, model_dir: Path) -> list[str]:
    """Store the claims, a model trained on them and the documents; return the documents' claims."""
    with Session(database) as session:
        store_made_up_claims(session)
        session.commit()
        model, report = train_and_score(load_training_data(session), 7)
        save_win_model(model, model_dir, split_seed=report.split_seed, splits=report.split_facts())
        claim_ids = list(
            session.scalars(
                select(ClaimSampleLabel.source_claim_id)
                .where(ClaimSampleLabel.is_denied, ClaimSampleLabel.split == DatasetSplit.TEST)
                .order_by(ClaimSampleLabel.source_claim_id)
                .limit(DOCUMENT_CLAIMS)
            )
        )
        for claim_id in claim_ids:
            for kind in (LETTER, NOTE):
                session.add(
                    GeneratedDocument(
                        source_claim_id=claim_id,
                        document_type=kind,
                        template_id="made_up_template",
                        seed=42,
                        generator_version="v1",
                        text=document_text(claim_id, kind),
                        answer_key=ANSWER_KEYS[kind],
                        noise_version="v1",
                        noise_record={
                            "level": "none",
                            "swaps": 0,
                            "drops": 0,
                            "missing_field": None,
                            "page_width": None,
                        },
                    )
                )
        session.commit()
    assert len(claim_ids) == DOCUMENT_CLAIMS
    return claim_ids


def _empty_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        for table in reversed(TABLES):  # rows that point at others go first
            connection.execute(delete(table))


def _store_results(engine: Engine, claim_ids: list[str]) -> None:
    """Store a fully right result for the letter and the note of each claim."""
    with Session(engine) as session:
        for claim_id in claim_ids:
            for kind in (LETTER, NOTE):
                save_extraction_row(session, result_row(stored_document(kind, claim_id)))
        session.commit()


def _row_counts(engine: Engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            table.__tablename__: session.scalar(select(func.count()).select_from(table)) or 0
            for table in COUNTED_TABLES
        }


def test_right_results_and_a_good_model_meet_the_targets_and_exit_0(
    database: Engine, claims: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _store_results(database, claims)

    exit_code = main(ARGS)

    assert exit_code == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].startswith("Stage 3 evaluation on the test split (generated data:")
    assert f"extraction prompt {PROMPT_VERSION} " in lines[1]
    assert "today 2008-06-01, amount floor $25" in lines[1]
    assert lines[2] == "documents: 8 | with a current result 8 | no result 0 | stale result 0"
    assert "  field accuracy, at least 85%: 100.00%, meets the target" in lines
    assert "  headline denial reason, at least 85%: 100.00%, meets the target" in lines
    assert "  same outcome: 4 of 4 (100.00%)" in lines
    assert any(line.startswith("  model AUC, at least 0.70: ") for line in lines)
    assert lines[-1] == MET
    assert captured.err == ""


def test_a_number_below_its_target_prints_the_report_and_exits_1(
    database: Engine, claims: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _store_results(database, claims[:3])  # one claim's letter and note have no result

    exit_code = main(ARGS)

    assert exit_code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[2] == "documents: 8 | with a current result 6 | no result 2 | stale result 0"
    assert "  field accuracy, at least 85%: 75.00%, below the target" in lines
    assert "  headline denial reason, at least 85%: 75.00%, below the target" in lines
    assert lines[-1] == NOT_MET


def test_no_extraction_result_counts_every_value_as_wrong_and_exits_1(
    database: Engine, claims: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(ARGS)

    assert exit_code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[2] == "documents: 8 | with a current result 0 | no result 8 | stale result 0"
    assert "  field accuracy, at least 85%: 0.00%, below the target" in lines
    assert lines[-1] == NOT_MET


def test_a_split_with_no_document_exits_1_with_a_plain_message(
    database: Engine, claims: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _store_results(database, claims)

    exit_code = main([*ARGS, "--split", "validation"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "the validation split has no document: run pipelines.generate_documents first\n"
    )


def test_no_saved_model_reports_the_extraction_half_and_exits_1(
    database: Engine, claims: list[str], model_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _store_results(database, claims)
    (model_dir / MODEL_FILE_NAME).unlink()
    (model_dir / FACTS_FILE_NAME).unlink()

    exit_code = main(ARGS)

    assert exit_code == 1
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert (
        f"win-probability model: cannot be measured (the facts file {FACTS_FILE_NAME} is"
        " missing: run pipelines.train_win_model first)"
    ) in lines
    assert "  field accuracy, at least 85%: 100.00%, meets the target" in lines
    assert "  model AUC, at least 0.70: cannot be measured" in lines
    assert lines[-1] == NOT_MET
    assert "Traceback" not in captured.err


def test_labels_of_another_rule_version_cannot_be_scored_and_exit_1(
    database: Engine, claims: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _store_results(database, claims)
    with database.begin() as connection:
        connection.execute(update(ClaimSampleLabel).values(label_rule_version="v0"))

    exit_code = main(ARGS)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "cannot be measured (labels were made with rule version v0, the code is at v2" in (
        captured.out
    )
    assert captured.out.splitlines()[-1] == NOT_MET
    assert "Traceback" not in captured.err


def test_labels_that_no_longer_fit_their_lines_are_named_once_with_a_count(
    database: Engine, claims: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with database.begin() as connection:
        connection.execute(delete(ClaimSampleLine))

    exit_code = main(ARGS)

    assert exit_code == 1
    out = capsys.readouterr().out
    assert out.count("the stored label no longer fits its stored lines") == 1
    assert f"(and {DENIED_COUNT - 1} more)" in out


@pytest.mark.parametrize(
    "arguments",
    [
        ["--prompt-version", PROMPT_VERSION],
        ["--rules-today", "yesterday"],
        ["--rules-today", "2010-02-30"],
        ["--rules-today", "2010-01-01", "--split", "holdout"],
    ],
    ids=["no rules today", "not a date", "no such day", "no such split"],
)
def test_bad_arguments_exit_2(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments)

    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_the_default_split_is_test_and_the_default_prompt_the_one_in_the_code(
    database: Engine, claims: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _store_results(database, claims)  # stored under the tests' prompt version, not the default

    exit_code = main(["--rules-today", "2008-06-01"])

    assert exit_code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Stage 3 evaluation on the test split ")
    assert f"extraction prompt {EXTRACTION_PROMPT_VERSION} " in lines[1]
    assert lines[2] == "documents: 8 | with a current result 0 | no result 8 | stale result 0"


def test_the_run_writes_nothing_and_calls_no_model(
    database: Engine, claims: list[str], model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_results(database, claims)

    def no_call(self: LlmGateway, request: object) -> None:
        raise AssertionError("the evaluation made a model call")

    monkeypatch.setattr(LlmGateway, "complete", no_call)
    rows_before = _row_counts(database)
    files_before = {path.name: path.read_bytes() for path in model_dir.iterdir()}

    assert main(ARGS) == 0

    assert _row_counts(database) == rows_before
    assert {path.name: path.read_bytes() for path in model_dir.iterdir()} == files_before


def test_the_command_and_the_evaluation_package_never_import_the_gateway() -> None:
    sources = [
        ROOT / "pipelines" / "run_evaluation.py",
        *(ROOT / "src" / "evaluation").glob("*.py"),
    ]

    assert len(sources) > 1
    for source in sources:
        assert "src.llm" not in source.read_text(encoding="utf-8")


def test_no_document_text_and_no_value_is_printed_or_logged(
    database: Engine,
    claims: list[str],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _store_results(database, claims)

    main(ARGS)

    captured = capsys.readouterr()
    for shown in (captured.out, captured.err, caplog.text):
        assert PATIENT_NAME not in shown
        assert "Made-up" not in shown
        assert "MBR-000001" not in shown
        assert claims[0] not in shown
