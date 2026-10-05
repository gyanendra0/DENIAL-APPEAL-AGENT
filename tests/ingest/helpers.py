"""Shared pieces for the loader tests. The fixture workbook is tiny and made up."""

from pathlib import Path

from openpyxl import load_workbook

from src.ingest.marketplace_denials import SHEET_NAME_TEMPLATE

CellValue = int | float | str | None
FIXTURE = Path(__file__).parent / "fixtures" / "tc_puf_sample.xlsx"
PLAN_YEAR = 2026
SHEET_NAME = SHEET_NAME_TEMPLATE.format(plan_year=PLAN_YEAR)


def edited_copy(tmp_path: Path, edits: dict[str, CellValue]) -> Path:
    """Copy the fixture with some cells replaced, e.g. {"E4": 123}."""
    workbook = load_workbook(FIXTURE)
    sheet = workbook[SHEET_NAME]
    for cell, value in edits.items():
        sheet[cell] = value
    path = tmp_path / "edited.xlsx"
    workbook.save(path)
    return path
