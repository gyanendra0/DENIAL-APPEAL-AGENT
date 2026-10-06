"""Shared pieces for the loader tests. The fixture files are tiny and made up."""

import csv
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import load_workbook

from src.ingest.claims_sample import COLUMN
from src.ingest.marketplace_denials import SHEET_NAME_TEMPLATE

CellValue = int | float | str | None
FIXTURE = Path(__file__).parent / "fixtures" / "tc_puf_sample.xlsx"
PLAN_YEAR = 2026
SHEET_NAME = SHEET_NAME_TEMPLATE.format(plan_year=PLAN_YEAR)
CLAIMS_FIXTURE = Path(__file__).parent / "fixtures" / "carrier_claims_sample.csv"
CLAIMS_CSV_NAME = "carrier_claims.csv"


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
