"""Shared pieces for the loader tests. The fixture files are tiny and made up."""

import csv
import io
from collections.abc import Sequence
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import load_workbook
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

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
MANUAL_FIXTURE = Path(__file__).parent / "fixtures" / "manual_chapter_sample.pdf"
# The lines of the manual fixture, page by page. `write_text_pdf` made the fixture from them.
MANUAL_PAGES: tuple[tuple[str, ...], ...] = (
    (
        "    Medicare Benefit Policy Manual",
        "Chapter 99 - Made-Up Example Services",
        "",
        "Table of Contents",
        "(Rev. 500; Issued: 03-04-25)",
        "",
        "Transmittals for Chapter 99",
        "",
        "10 - Example Coverage Rules",
        "10.1 - A Longer Example Title That Wraps",
        "onto a Second Line",
        " 20 – Parent Heading Only",
        "20.1 - Example With a Short Revision Line",
        "30 - Section Without a Revision Line",
        "",
        "10 - Example Coverage Rules",
        "(Rev. 12, Issued: 01-02-24, Effective: 03-04-24, Implementation: 03-05-24)",
        "",
        "The fixture clinic covers a made-up service when both example conditions",
        "are met.  This paragraph runs over the end of the page and stops in the",
    ),
    (
        "middle of a sentence.",
        "",
        "A.  Example Sub-Heading",
        "",
        "The example patient may opt-",
        "out of the made-up plan after the",
        "30 - day example period, or after a pre-",
        "Example review.",
        "10.1 - A Longer Example Title That Wraps",
        "onto a Second Line",
        "(Rev. 34; Issued: 05-06-2024; Effective: 07-08-2024; Implementation: 07-",
        "09-2024)",
        "B3-9999",
        "",
        "• The made-up supplier’s example bullet (§10).",
        "",
        "20 – Parent Heading Only",
        "(Rev. 1, 10-01-03)",
        "",
        "20.1 - Example With a Short Revision Line",
        "(Rev. 7, 11-12-05)",
        "",
        "This section's revision line names no effective date.",
        "",
        "30 - Section Without a Revision Line",
        "",
        "This section has a heading and no revision line.",
        "",
        "Transmittals Issued for this Chapter",
        "",
        "Rev # Issue Date Subject Impl Date CR#",
        "R500BP 03/04/2025 TRANSMITTAL-TABLE-MARKER 03/05/2025 1234",
    ),
)


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


def write_text_pdf(path: Path, pages: Sequence[Sequence[str]]) -> Path:
    """Write a PDF with the given lines on each page, in a standard font. No lines, no text.

    The PDF library returns each line followed by a space, an empty line as a line of one
    space, and no line end after a page's last line: the way it returns the real manual chapters.
    """
    writer = PdfWriter()
    # `_add_object` is the library's only way to store an object that pages point at.
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    for lines in pages:
        page = writer.add_blank_page(width=612, height=792)
        shown = [
            b"<" + f"{line} ".encode("cp1252").hex().encode("ascii") + b"> Tj" for line in lines
        ]
        operations = [b"BT /F1 10 Tf 12 TL 72 740 Td", b" T* ".join(shown), b"ET"]
        content = StreamObject()
        content.set_data(b"\n".join(operations))
        page[NameObject("/Contents")] = writer._add_object(content)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def manual_pdf(
    tmp_path: Path,
    replace: dict[str, str | None] | None = None,
    *,
    name: str = "chapter.pdf",
) -> Path:
    """Write the manual fixture with some lines replaced, e.g. {"B3-9999": "PM-1"}.

    A key is a whole line of `MANUAL_PAGES`; every line equal to it is replaced, and None
    removes it.
    """
    replace = replace or {}
    unknown = set(replace) - {line for page in MANUAL_PAGES for line in page}
    if unknown:
        raise ValueError(f"not a line of the fixture: {sorted(unknown)}")
    pages = [
        [replace.get(line, line) for line in page if replace.get(line, line) is not None]
        for page in MANUAL_PAGES
    ]
    return write_text_pdf(tmp_path / name, [[line or "" for line in page] for page in pages])
