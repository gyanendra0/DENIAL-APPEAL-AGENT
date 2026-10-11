"""CLI tests. The command really commits, so each test cleans the tables afterwards."""

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, Insert, delete, select
from sqlalchemy.orm import Session

from pipelines.load_evidence_corpus import main as load_determinations
from pipelines.load_manual_chapters import main
from src.config.settings import get_settings
from src.db.models import EvidenceChunk, EvidenceDocument, EvidenceSource
from tests.ingest.helpers import (
    MANUAL_FIXTURE,
    MANUAL_PAGES,
    manual_pdf,
    ncd_zip,
    write_text_pdf,
)

FILE_DATE = ["--file-date", "2026-10-11"]
MANUAL_LINE = "    Medicare Benefit Policy Manual"
CHAPTER_LINE = "Chapter 99 - Made-Up Example Services"
MADE_UP_TEXT_MARKERS = ("Example", "made-up", "fixture")


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


def _stored_hashes(
    engine: Engine, source: EvidenceSource = EvidenceSource.CMS_MANUAL
) -> list[tuple[str, int, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                EvidenceDocument.source_document_id,
                EvidenceChunk.chunk_index,
                EvidenceChunk.text_sha256,
            )
            .join(EvidenceChunk, EvidenceChunk.evidence_document_id == EvidenceDocument.id)
            .where(EvidenceDocument.source == source)
            .order_by(EvidenceDocument.source_document_id, EvidenceChunk.chunk_index)
        )
        return [(row[0], row[1], row[2]) for row in rows]


def _second_chapter(tmp_path: Path) -> Path:
    """The fixture again, as chapter 7 of the other manual."""
    return manual_pdf(
        tmp_path,
        {
            MANUAL_LINE: "Medicare Claims Processing Manual",
            CHAPTER_LINE: "Chapter 7 - Made-Up Example Services",
        },
        name="second.pdf",
    )


def test_loads_the_files_and_commits(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([str(MANUAL_FIXTURE), str(_second_chapter(tmp_path)), *FILE_DATE])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(_stored_hashes(database)) == 8
    assert captured.out.splitlines() == [
        "loaded 8 documents and 8 chunks (chunker v1)",
        "  chapter 100-02-99: 4 documents, 4 chunks",
        "  chapter 100-04-7: 4 documents, 4 chunks",
        "  sections with no text left out: 2",
        "  sections without a revision line (version 1, the file date): 2",
        "  sections with the issued date as effective date: 2",
        "  file date: 2026-10-11",
        "  chunk length: longest 309, median 54",
    ]
    assert not any(marker in captured.out for marker in MADE_UP_TEXT_MARKERS)


def test_the_file_date_is_stored_with_every_section(database: Engine) -> None:
    main([str(MANUAL_FIXTURE), "--file-date", "2026-09-30"])

    with database.connect() as connection:
        dates = connection.scalars(select(EvidenceDocument.source_file_date).distinct()).all()
    assert dates == [date(2026, 9, 30)]


def test_running_twice_keeps_the_same_rows_and_hashes(database: Engine) -> None:
    main([str(MANUAL_FIXTURE), *FILE_DATE])
    first = _stored_hashes(database)

    exit_code = main([str(MANUAL_FIXTURE), *FILE_DATE])

    assert exit_code == 0
    assert _stored_hashes(database) == first
    assert len(first) == 4


def test_a_chapter_that_is_not_named_is_removed(database: Engine, tmp_path: Path) -> None:
    second = str(_second_chapter(tmp_path))
    main([str(MANUAL_FIXTURE), second, *FILE_DATE])

    exit_code = main([second, *FILE_DATE])

    assert exit_code == 0
    assert {document_id.rsplit("-", 1)[0] for document_id, _, _ in _stored_hashes(database)} == {
        "100-04-7"
    }


def test_the_stored_determinations_stay_as_they_were(database: Engine, tmp_path: Path) -> None:
    load_determinations([str(ncd_zip(tmp_path))])
    before = _stored_hashes(database, EvidenceSource.CMS_NCD)

    exit_code = main([str(MANUAL_FIXTURE), *FILE_DATE])

    assert exit_code == 0
    assert _stored_hashes(database, EvidenceSource.CMS_NCD) == before
    assert len(before) == 6
    assert len(_stored_hashes(database)) == 4


def _not_a_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "notes.pdf"
    path.write_bytes(b"made-up bytes, not a pdf")
    return path


def _no_text(tmp_path: Path) -> Path:
    return write_text_pdf(tmp_path / "scan.pdf", [[], []])


def _no_numbered_section(tmp_path: Path) -> Path:
    return write_text_pdf(
        tmp_path / "plain.pdf",
        [[MANUAL_LINE, CHAPTER_LINE, "", "Table of Contents", "", "Made-up text, no sections."]],
    )


def _page_footer(tmp_path: Path) -> Path:
    return write_text_pdf(
        tmp_path / "footer.pdf", [[*page, "MADE-UP-FOOTER Example Manual"] for page in MANUAL_PAGES]
    )


@pytest.mark.parametrize(
    ("bad_file", "problem"),
    [
        (_not_a_pdf, "notes.pdf: not a readable PDF file"),
        (_no_text, "scan.pdf: the PDF holds no text that can be extracted"),
        (_no_numbered_section, "plain.pdf: no numbered section"),
        (
            _page_footer,
            "footer.pdf: the same line is at the bottom of 2 of 2 pages: "
            "a page header, footer or page number",
        ),
    ],
)
def test_each_gate_exits_1_and_writes_nothing(
    database: Engine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bad_file: Any,
    problem: str,
) -> None:
    exit_code = main([str(MANUAL_FIXTURE), str(bad_file(tmp_path)), *FILE_DATE])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "batch rejected, 1 problem(s):" in captured.err
    assert problem in captured.err
    assert "Traceback" not in captured.err
    assert _stored_hashes(database) == []


def test_rejected_file_keeps_the_old_rows(
    database: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main([str(MANUAL_FIXTURE), *FILE_DATE])
    before = _stored_hashes(database)

    exit_code = main([str(_not_a_pdf(tmp_path)), *FILE_DATE])

    assert exit_code == 1
    assert _stored_hashes(database) == before
    assert len(before) == 4


def test_the_same_file_twice_exits_1(database: Engine, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([str(MANUAL_FIXTURE), str(MANUAL_FIXTURE), *FILE_DATE])

    assert exit_code == 1
    assert "section 100-02-99-10 appears in more than one file" in capsys.readouterr().err
    assert _stored_hashes(database) == []


def test_failure_while_writing_chunks_keeps_the_old_rows(
    database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main([str(MANUAL_FIXTURE), *FILE_DATE])
    before = _stored_hashes(database)

    def fail_on_chunks(self: Session, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(statement, Insert) and statement.table.name == EvidenceChunk.__tablename__:
            raise RuntimeError("database went away")
        return real_execute(self, statement, *args, **kwargs)

    real_execute = Session.execute
    monkeypatch.setattr(Session, "execute", fail_on_chunks)

    with pytest.raises(RuntimeError, match="database went away"):
        main([str(_second_chapter(tmp_path)), *FILE_DATE])

    # The old rows were deleted and the new documents written, then all of it rolled back.
    assert _stored_hashes(database) == before
    assert len(before) == 4


def test_missing_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(MANUAL_FIXTURE), str(tmp_path / "nope.pdf"), *FILE_DATE])

    assert excinfo.value.code == 2
    assert "file not found" in capsys.readouterr().err


def test_a_folder_as_a_path_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path), *FILE_DATE])

    assert excinfo.value.code == 2
    assert "file not found" in capsys.readouterr().err


def test_no_path_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(FILE_DATE)

    assert excinfo.value.code == 2
    assert "paths" in capsys.readouterr().err


def test_no_file_date_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(MANUAL_FIXTURE)])

    assert excinfo.value.code == 2
    assert "--file-date" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["11-10-2026", "2026-13-01", "yesterday", ""])
def test_a_file_date_that_is_not_a_date_exits_2(
    capsys: pytest.CaptureFixture[str], value: str
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(MANUAL_FIXTURE), "--file-date", value])

    assert excinfo.value.code == 2
    assert "not a date written as YYYY-MM-DD" in capsys.readouterr().err


def test_a_file_date_in_the_future_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    with pytest.raises(SystemExit) as excinfo:
        main([str(MANUAL_FIXTURE), "--file-date", tomorrow])

    assert excinfo.value.code == 2
    assert "--file-date is in the future" in capsys.readouterr().err


def test_a_file_date_of_today_is_accepted(database: Engine) -> None:
    assert main([str(MANUAL_FIXTURE), "--file-date", date.today().isoformat()]) == 0
