"""CLI tests. The command really commits, so each test cleans the tables afterwards."""

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, delete, func, select

from pipelines import load_claims_sample
from pipelines.load_claims_sample import DEFAULT_MAX_CLAIMS, main
from src.config.settings import get_settings
from src.db.base import Base
from src.db.models import ClaimSample, ClaimSampleLine
from src.ingest import claims_sample
from src.ingest.claims_sample import ClaimSampleBatch
from src.ingest.marketplace_denials import upsert_rows
from tests.ingest.helpers import claims_zip


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
        connection.execute(delete(ClaimSampleLine))
        connection.execute(delete(ClaimSample))


def _stored_count(engine: Engine, table: type[Base]) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table)) or 0


def _stored_counts(engine: Engine) -> tuple[int, int]:
    """(claim rows, line rows)."""
    return _stored_count(engine, ClaimSample), _stored_count(engine, ClaimSampleLine)


def test_loads_the_file_and_commits(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([str(claims_zip(tmp_path))])

    assert exit_code == 0
    assert _stored_counts(database) == (6, 9)
    assert "loaded 6 claims and 9 service lines" in capsys.readouterr().out


def test_running_twice_keeps_the_same_rows(database: Engine, tmp_path: Path) -> None:
    path = str(claims_zip(tmp_path))
    main([path])

    exit_code = main([path])

    assert exit_code == 0
    assert _stored_counts(database) == (6, 9)


def test_max_claims_limits_how_many_are_loaded(database: Engine, tmp_path: Path) -> None:
    exit_code = main([str(claims_zip(tmp_path)), "--max-claims", "2"])

    assert exit_code == 0
    assert _stored_counts(database) == (2, 4)


def test_reads_50000_claims_when_no_limit_is_given(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked_for: list[int | None] = []

    def fake_read(path: Path, max_claims: int | None = None) -> ClaimSampleBatch:
        asked_for.append(max_claims)
        return ClaimSampleBatch(claims=[], lines=[])

    monkeypatch.setattr(load_claims_sample, "read_claim_sample_rows", fake_read)

    main([str(claims_zip(tmp_path))])

    assert asked_for == [DEFAULT_MAX_CLAIMS] == [50_000]


def test_rejected_file_exits_1_and_writes_nothing(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_file = claims_zip(tmp_path, {(7, "LINE_NCH_PMT_AMT_1"): "-550.00"})  # the last row

    exit_code = main([str(bad_file)])

    err = capsys.readouterr().err
    assert exit_code == 1
    assert "batch rejected" in err
    assert "row 7: line 1: payment_amount" in err
    assert _stored_counts(database) == (0, 0)


def test_failure_while_writing_lines_also_undoes_the_claims(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_on_lines(
        session: Any, table: type[Base], rows: Sequence[BaseModel], *rest: Any
    ) -> int:
        if table is ClaimSampleLine:
            raise RuntimeError("database went away")
        return upsert_rows(session, table, rows, *rest)

    monkeypatch.setattr(claims_sample, "upsert_rows", fail_on_lines)

    with pytest.raises(RuntimeError, match="database went away"):
        main([str(claims_zip(tmp_path))])

    assert _stored_counts(database) == (0, 0)  # the claims were written, then rolled back


def test_missing_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path / "nope.zip")])

    assert excinfo.value.code == 2
    assert "file not found" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-5", "many"])
def test_bad_max_claims_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(claims_zip(tmp_path)), "--max-claims", value])

    assert excinfo.value.code == 2
    assert "--max-claims" in capsys.readouterr().err
