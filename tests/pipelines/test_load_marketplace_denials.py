"""CLI tests. The command really commits, so each test cleans the tables afterwards."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, func, select

from pipelines import load_marketplace_denials
from pipelines.load_marketplace_denials import main
from src.config.settings import get_settings
from src.db.base import Base
from src.db.models import IssuerDenialStats, PlanDenialStats
from tests.ingest.helpers import FIXTURE, edited_copy


@pytest.fixture
def database(
    engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Point the command at the test database, and empty the tables before and afterwards."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    get_settings.cache_clear()
    _empty_tables(engine)  # in case an earlier run was killed before its clean-up
    yield engine
    get_settings.cache_clear()
    _empty_tables(engine)


def _empty_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(delete(PlanDenialStats))
        connection.execute(delete(IssuerDenialStats))


def _stored_count(engine: Engine, table: type[Base]) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table)) or 0


def _stored_counts(engine: Engine) -> tuple[int, int]:
    """(issuer rows, plan rows)."""
    return _stored_count(engine, IssuerDenialStats), _stored_count(engine, PlanDenialStats)


def test_loads_the_file_and_commits(database: Engine, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([str(FIXTURE), "--plan-year", "2026"])

    assert exit_code == 0
    assert _stored_counts(database) == (3, 6)
    assert "loaded 3 issuer rows and 6 plan rows for plan year 2026" in capsys.readouterr().out


def test_running_twice_keeps_the_same_rows(database: Engine) -> None:
    main([str(FIXTURE), "--plan-year", "2026"])

    exit_code = main([str(FIXTURE), "--plan-year", "2026"])

    assert exit_code == 0
    assert _stored_counts(database) == (3, 6)


def test_rejected_file_exits_1_and_writes_nothing(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_file = edited_copy(tmp_path, {"E4": 123})  # issuer id that is not five digits

    exit_code = main([str(bad_file), "--plan-year", "2026"])

    assert exit_code == 1
    assert "batch rejected" in capsys.readouterr().err
    assert _stored_counts(database) == (0, 0)


def test_bad_plan_row_exits_1_and_changes_neither_table(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Unknown plan type: the issuer part of the row is fine.
    bad_file = edited_copy(tmp_path, {"I4": "XYZ"})

    exit_code = main([str(bad_file), "--plan-year", "2026"])

    assert exit_code == 1
    assert "row 4: plan_type" in capsys.readouterr().err
    assert _stored_counts(database) == (0, 0)


def test_failure_while_writing_plans_also_undoes_the_issuers(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object) -> int:
        raise RuntimeError("database went away")

    monkeypatch.setattr(load_marketplace_denials, "upsert_plan_denial_rows", fail)

    with pytest.raises(RuntimeError, match="database went away"):
        main([str(FIXTURE), "--plan-year", "2026"])

    assert _stored_counts(database) == (0, 0)  # the issuer rows were written, then rolled back


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
