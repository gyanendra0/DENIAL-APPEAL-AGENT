"""CLI tests. The command really commits, so each test cleans the tables afterwards."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, Insert, delete, func, select
from sqlalchemy.orm import Session

from pipelines.load_evidence_corpus import main
from src.config.settings import get_settings
from src.db.base import Base
from src.db.models import EvidenceChunk, EvidenceDocument
from tests.ingest.helpers import ncd_zip


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
        connection.execute(delete(EvidenceChunk))
        connection.execute(delete(EvidenceDocument))


def _stored_count(engine: Engine, table: type[Base]) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table)) or 0


def _stored_counts(engine: Engine) -> tuple[int, int]:
    """(document rows, chunk rows)."""
    return _stored_count(engine, EvidenceDocument), _stored_count(engine, EvidenceChunk)


def _stored_hashes(engine: Engine) -> list[tuple[str, int, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                EvidenceDocument.source_document_id,
                EvidenceChunk.chunk_index,
                EvidenceChunk.text_sha256,
            )
            .join(EvidenceChunk, EvidenceChunk.evidence_document_id == EvidenceDocument.id)
            .order_by(EvidenceDocument.source_document_id, EvidenceChunk.chunk_index)
        )
        return [(row[0], row[1], row[2]) for row in rows]


def test_loads_the_file_and_commits(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([str(ncd_zip(tmp_path))])

    assert exit_code == 0
    assert _stored_counts(database) == (4, 6)
    assert capsys.readouterr().out.splitlines() == [
        "loaded 4 documents and 6 chunks (chunker v1)",
        "  retired notices left out: 1",
        "  file date: 2026-10-05",
        "  chunk length: longest 150, median 60",
    ]


def test_running_twice_keeps_the_same_rows_and_hashes(database: Engine, tmp_path: Path) -> None:
    path = str(ncd_zip(tmp_path))
    main([path])
    first = _stored_hashes(database)

    exit_code = main([path])

    assert exit_code == 0
    assert _stored_hashes(database) == first
    assert _stored_counts(database) == (4, 6)


def test_a_new_file_replaces_the_stored_determinations(database: Engine, tmp_path: Path) -> None:
    main([str(ncd_zip(tmp_path))])
    retired_now = {(2, "NCD_mnl_sect_title"): "Example Blanket Therapy - RETIRED"}

    exit_code = main([str(ncd_zip(tmp_path, retired_now))])

    assert exit_code == 0
    assert _stored_counts(database) == (3, 4)


def test_rejected_file_exits_1_and_keeps_the_old_rows(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main([str(ncd_zip(tmp_path))])
    before = _stored_hashes(database)
    capsys.readouterr()

    exit_code = main([str(ncd_zip(tmp_path, {(6, "NCD_id"): "1"}))])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "batch rejected, 1 problem(s):" in captured.err
    assert "row 6: NCD_id appears more than once in the file" in captured.err
    assert "Traceback" not in captured.err
    assert _stored_hashes(database) == before


@pytest.mark.parametrize(
    ("edits", "problem"),
    [
        ({(3, "NCD_mnl_sect_title"): ""}, "row 3: title: "),
        ({(5, "NCD_id"): "2"}, "row 5: NCD_id appears more than once in the file"),
        ({(5, "itm_srvc_desc"): "<p> </p>"}, "row 5: no policy text"),
        (
            {(3, "indctn_lmtn"): "<h3>Made-up heading</h3>Made-up text."},
            "row 3: the policy text holds the unknown tag <h3>",
        ),
        (
            {(3, "indctn_lmtn"): "<p>dam\x00ged</p>"},
            "row 3: the policy text holds an unreadable character",
        ),
    ],
)
def test_each_gate_exits_1_and_writes_nothing(
    database: Engine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    edits: dict[tuple[int, str], str],
    problem: str,
) -> None:
    exit_code = main([str(ncd_zip(tmp_path, edits))])

    assert exit_code == 1
    assert problem in capsys.readouterr().err
    assert _stored_counts(database) == (0, 0)


def test_failure_while_writing_chunks_keeps_the_old_rows(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main([str(ncd_zip(tmp_path))])
    before = _stored_hashes(database)

    def fail_on_chunks(self: Session, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(statement, Insert) and statement.table.name == EvidenceChunk.__tablename__:
            raise RuntimeError("database went away")
        return real_execute(self, statement, *args, **kwargs)

    real_execute = Session.execute
    monkeypatch.setattr(Session, "execute", fail_on_chunks)

    with pytest.raises(RuntimeError, match="database went away"):
        main([str(ncd_zip(tmp_path))])

    # The old rows were deleted and the new documents written, then all of it rolled back.
    assert _stored_hashes(database) == before
    assert len(before) == 6


def test_missing_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path / "nope.zip")])

    assert excinfo.value.code == 2
    assert "file not found" in capsys.readouterr().err


def test_a_folder_as_the_path_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path)])

    assert excinfo.value.code == 2
    assert "file not found" in capsys.readouterr().err


def test_no_path_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2
    assert "path" in capsys.readouterr().err
