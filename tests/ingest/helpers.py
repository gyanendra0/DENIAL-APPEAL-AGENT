"""Shared pieces for the loader tests. The fixture files are tiny and made up."""

import csv
import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import load_workbook

from src.ingest.claims_sample import COLUMN
from src.ingest.evidence_corpus import COLUMN as NCD_COLUMN
from src.ingest.evidence_corpus import CSV_NAME, NESTED_ARCHIVE_NAME
from src.ingest.marketplace_denials import SHEET_NAME_TEMPLATE

CellValue = int | float | str | None
FIXTURE = Path(__file__).parent / "fixtures" / "tc_puf_sample.xlsx"
PLAN_YEAR = 2026
SHEET_NAME = SHEET_NAME_TEMPLATE.format(plan_year=PLAN_YEAR)
CLAIMS_FIXTURE = Path(__file__).parent / "fixtures" / "carrier_claims_sample.csv"
CLAIMS_CSV_NAME = "carrier_claims.csv"
NCD_FIXTURE = Path(__file__).parent / "fixtures" / "ncd_trkg_sample.csv"
NCD_FILE_DATE = (2026, 10, 5)


def edited_copy(tmp_path: Path, edits: dict[str, CellValue]) -> Path:
    """Copy the fixture with some cells replaced, e.g. {"E4": 123}."""
    workbook = load_workbook(FIXTURE)
    sheet = workbook[SHEET_NAME]
    for cell, value in edits.items():
        sheet[cell] = value
    path = tmp_path / "edited.xlsx"
    workbook.save(path)
    return path


def zipped(tmp_path: Path, content: bytes, member: str = CLAIMS_CSV_NAME) -> Path:
    """Write a zip holding one member, the way the claims source is published."""
    path = tmp_path / "claims.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(member, content)
    return path


def claims_zip(tmp_path: Path, edits: dict[tuple[int, str], str] | None = None) -> Path:
    """Zip the claims fixture with some cells replaced, e.g. {(2, "CLM_ID"): "123"}.

    The row number is the line in the csv: the header is row 1, the first claim is row 2.
    Lines end with CRLF, like the source.
    """
    with CLAIMS_FIXTURE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    for (row_number, header), value in (edits or {}).items():
        rows[row_number - 1][COLUMN[header]] = value
    text = "".join(",".join(cells) + "\r\n" for cells in rows)
    return zipped(tmp_path, text.encode("utf-8"))


def ncd_csv(edits: dict[tuple[int, str], str] | None = None) -> bytes:
    """The determinations fixture as csv bytes, with some cells replaced.

    The row number counts csv records: the header is row 1, the first determination is row 2.
    Records end with CRLF, like the source.
    """
    with NCD_FIXTURE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    for (row_number, header), value in (edits or {}).items():
        rows[row_number - 1][NCD_COLUMN[header]] = value
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    return buffer.getvalue().encode("utf-8")


def ncd_zip(
    tmp_path: Path,
    edits: dict[tuple[int, str], str] | None = None,
    *,
    content: bytes | None = None,
    member: str = CSV_NAME,
    nested_name: str = NESTED_ARCHIVE_NAME,
    file_date: tuple[int, int, int] = NCD_FILE_DATE,
) -> Path:
    """Write the determinations download: a zip holding a zip that holds the csv.

    `content` replaces the csv bytes; `file_date` is the date the zip records for the csv.
    """
    nested = io.BytesIO()
    with ZipFile(nested, "w", ZIP_DEFLATED) as archive:
        info = ZipInfo(member, date_time=(*file_date, 6, 46, 50))
        info.compress_type = ZIP_DEFLATED
        archive.writestr(info, ncd_csv(edits) if content is None else content)
    path = tmp_path / "ncd.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("readme_first.txt", b"made-up read me")
        archive.writestr(nested_name, nested.getvalue())
    return path
