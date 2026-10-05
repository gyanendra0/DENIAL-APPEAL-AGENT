"""CLI tests. The command really commits, so each test cleans the table afterwards."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import Engine, delete, func, select

from pipelines.load_marketplace_denials import main
from src.config.settings import get_settings
from src.db.models import IssuerDenialStats
from src.ingest.marketplace_denials import SHEET_NAME

FIXTURE = Path(__file__).parents[1] / "ingest" / "fixtures" / "tc_puf_sample.xlsx"


@pytest.fixture
def database(
    engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Point the command at the test database, and empty the table afterwards."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    get_settings.cache_clear()
    yield engine
    get_settings.cache_clear()
    with engine.begin() as connection:
        connection.execute(delete(IssuerDenialStats))


def _stored_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(IssuerDenialStats)) or 0


def test_loads_the_file_and_commits(database: Engine, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([str(FIXTURE), "--plan-year", "2026"])

    assert exit_code == 0
    assert _stored_count(database) == 3
    assert "loaded 3 issuer rows for plan year 2026" in capsys.readouterr().out


def test_running_twice_keeps_the_same_rows(database: Engine) -> None:
    main([str(FIXTURE), "--plan-year", "2026"])

    exit_code = main([str(FIXTURE), "--plan-year", "2026"])

    assert exit_code == 0
    assert _stored_count(database) == 3


def test_rejected_file_exits_1_and_writes_nothing(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workbook = load_workbook(FIXTURE)
    workbook[SHEET_NAME]["E4"] = 123  # issuer id that is not five digits
    bad_file = tmp_path / "bad.xlsx"
    workbook.save(bad_file)

    exit_code = main([str(bad_file), "--plan-year", "2026"])

    assert exit_code == 1
    assert "batch rejected" in capsys.readouterr().err
    assert _stored_count(database) == 0


def test_missing_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path / "nope.xlsx"), "--plan-year", "2026"])

    assert excinfo.value.code == 2
    assert "file not found" in capsys.readouterr().err


def test_missing_plan_year_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(FIXTURE)])

    assert excinfo.value.code == 2
    assert "--plan-year" in capsys.readouterr().err
