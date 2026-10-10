"""Load the CMS national coverage determinations ("Current NCD Data", `ncd.zip`) as evidence.

The download is a zip that holds a second zip, `ncd_csv.zip`, and that holds `ncd_trkg.csv`:
one row per determination. Each row becomes one document, and its policy text is cut into
chunks (`src.rag.chunking`). A file that fails any quality gate is rejected outright: nothing
is returned. The rows then replace the stored ones in `evidence_documents` and
`evidence_chunks`, public reference tables (no `account_id`).

Only two text columns are read, `itm_srvc_desc` and `indctn_lmtn`: they are the policy text.
The revision history, the cross references and the "other" text are never stored. A
determination whose title is marked `RETIRED` is a short notice, not policy: it is counted
and left out. The file states no release date, so the date kept is the one the zip records
for `ncd_trkg.csv`.
"""

import csv
import io
import logging
import re
import zlib
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from src.db.models import EvidenceChunk, EvidenceDocument, EvidenceSource
from src.ingest.html_paragraphs import html_to_paragraphs
from src.ingest.marketplace_denials import BatchRejectedError, describe_validation_error
from src.rag.chunking import Chunk, SectionText, chunk_document

logger = logging.getLogger(__name__)

NESTED_ARCHIVE_NAME = "ncd_csv.zip"
CSV_NAME = "ncd_trkg.csv"
ID_HEADER = "NCD_id"
VERSION_HEADER = "NCD_vrsn_num"
SECTION_NUMBER_HEADER = "NCD_mnl_sect"
TITLE_HEADER = "NCD_mnl_sect_title"
EFFECTIVE_DATE_HEADER = "NCD_efctv_dt"
DESCRIPTION_HEADER = "itm_srvc_desc"
INDICATIONS_HEADER = "indctn_lmtn"
AMA_NOTICE_HEADER = "NCD_AMA"
EXPECTED_HEADER = (
    ID_HEADER,
    VERSION_HEADER,
    "natl_cvrg_type",
    "cvrg_lvl_cd",
    SECTION_NUMBER_HEADER,
    TITLE_HEADER,
    EFFECTIVE_DATE_HEADER,
    "NCD_impltn_dt",
    "NCD_trmntn_dt",
    DESCRIPTION_HEADER,
    INDICATIONS_HEADER,
    "xref_txt",
    "othr_txt",
    "trnsmtl_num",
    "trnsmtl_issnc_dt",
    "trnsmtl_url",
    "chg_rqst_num",
    "pblctn_cd",
    "rev_hstry",
    "under_rvw",
    "creatd_tmstmp",
    "last_updt_tmstmp",
    "last_clrnc_tmstmp",
    "NCD_lab",
    "ncd_keyword",
    AMA_NOTICE_HEADER,
)
COLUMN = {name: index for index, name in enumerate(EXPECTED_HEADER)}
# The policy columns in reading order, each with the section title its chunks get. The titles
# are the data dictionary's field descriptions, shortened; they are not text of the file.
POLICY_SECTIONS = (
    (DESCRIPTION_HEADER, "Item/Service Description"),
    (INDICATIONS_HEADER, "Indications and Limitations of Coverage"),
)
# A title holding this word is a retirement notice.
RETIRED_MARKER = "RETIRED"
# The file writes every date as a timestamp; the time of an effective date is always midnight.
SOURCE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
SOURCE_TIMESTAMP_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")
SOURCE_INTEGER_PATTERN = re.compile(r"[0-9]+")
SOURCE_TRUE = "True"
SOURCE_FALSE = "False"
# What a decoder leaves in place of bytes it could not read.
REPLACEMENT_CHARACTER = "�"
HEADER_ROW = 1
UNREADABLE_ZIP = "not a readable .zip file"
# Raised for damaged content, and `RuntimeError` for an encrypted member.
DAMAGED_ZIP_ERRORS = (BadZipFile, zlib.error, EOFError, RuntimeError)


class EvidenceDocumentRow(BaseModel):
    """One validated determination, shaped like the `evidence_documents` table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EvidenceSource
    source_document_id: str = Field(pattern=r"^[1-9][0-9]*$", max_length=40)
    section_number: str = Field(pattern=r"^[0-9]+(\.[0-9]+){1,3}$", max_length=20)
    title: str = Field(min_length=1, max_length=300)
    version_number: int = Field(ge=1)
    effective_date: date
    source_file_date: date

    @field_validator("title", mode="before")
    @classmethod
    def _single_spaces(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("version_number", mode="before")
    @classmethod
    def _source_text_is_a_plain_integer(cls, value: Any) -> Any:
        if isinstance(value, str) and not SOURCE_INTEGER_PATTERN.fullmatch(value):
            raise ValueError("must be a whole number written in digits")
        return value

    @field_validator("effective_date", mode="before")
    @classmethod
    def _source_text_to_date(cls, value: Any) -> Any:
        if isinstance(value, str):
            if not SOURCE_TIMESTAMP_PATTERN.fullmatch(value):
                raise ValueError("must be a date written as YYYY-MM-DD HH:MM:SS")
            try:
                return datetime.strptime(value, SOURCE_TIMESTAMP_FORMAT).date()
            except ValueError:
                raise ValueError("must be a date written as YYYY-MM-DD HH:MM:SS") from None
        return value


class EvidenceCorpusEntry(BaseModel):
    """One determination with the chunks of its policy text, in reading order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document: EvidenceDocumentRow
    chunks: tuple[Chunk, ...] = Field(min_length=1)


class EvidenceCorpusBatch(BaseModel):
    """The determinations read from one file, and how many retired notices were left out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: list[EvidenceCorpusEntry]
    retired_skipped: int = Field(ge=0)

    @property
    def chunk_count(self) -> int:
        return sum(len(entry.chunks) for entry in self.entries)


def read_evidence_corpus(path: Path) -> EvidenceCorpusBatch:
    """Read the determinations zip at `path` and return its validated documents with chunks.

    Raises `BatchRejectedError` if any gate fails.
    """
    rows, source_file_date = _csv_rows(path)
    problems = _duplicate_problems(rows, ID_HEADER) + _duplicate_problems(
        rows, SECTION_NUMBER_HEADER
    )
    entries: list[EvidenceCorpusEntry] = []
    retired_skipped = 0
    for row_number, cells in enumerate(rows, start=HEADER_ROW + 1):
        if len(cells) != len(EXPECTED_HEADER):
            problems.append(
                f"row {row_number}: has {len(cells)} fields, expected {len(EXPECTED_HEADER)}"
            )
            continue
        if RETIRED_MARKER in cells[COLUMN[TITLE_HEADER]]:
            retired_skipped += 1
            continue
        entry, row_problems = _entry(cells, row_number, source_file_date)
        problems.extend(row_problems)
        if entry is not None:
            entries.append(entry)
    if not rows:
        problems.append("no data rows")
    elif not entries and not problems:
        problems.append(f"nothing to load: all {retired_skipped} rows are retired notices")
    if problems:
        raise BatchRejectedError(problems)

    batch = EvidenceCorpusBatch(entries=entries, retired_skipped=retired_skipped)
    logger.info(
        "read %d determinations with %d chunks, left out %d retired notices",
        len(entries),
        batch.chunk_count,
        retired_skipped,
    )
    return batch


def replace_evidence_corpus(session: Session, batch: EvidenceCorpusBatch) -> tuple[int, int]:
    """Make the stored determinations exactly `batch`: remove them all, then store `batch`.

    Only rows of the source `cms_ncd` are removed; their chunks go with them. With an upsert
    alone, a determination that left the file would keep its old chunks. Returns (documents,
    chunks) written. Does not commit: the caller owns the transaction, so a failure brings
    the old rows back.
    """
    session.execute(
        delete(EvidenceDocument).where(EvidenceDocument.source == EvidenceSource.CMS_NCD)
    )
    if not batch.entries:
        return 0, 0
    stored = session.execute(
        insert(EvidenceDocument).returning(
            EvidenceDocument.source_document_id, EvidenceDocument.id
        ),
        [entry.document.model_dump() for entry in batch.entries],
    )
    document_ids = {source_document_id: row_id for source_document_id, row_id in stored}
    session.execute(
        insert(EvidenceChunk),
        [
            {
                "evidence_document_id": document_ids[entry.document.source_document_id],
                **chunk.model_dump(),
            }
            for entry in batch.entries
            for chunk in entry.chunks
        ],
    )
    logger.info("stored %d evidence documents and %d chunks", len(batch.entries), batch.chunk_count)
    return len(batch.entries), batch.chunk_count


def _csv_rows(path: Path) -> tuple[list[list[str]], date]:
    """Return every data row of `ncd_trkg.csv` and the date the zip records for that file.

    Raises `BatchRejectedError` for an unreadable zip, a missing member, text that is not
    UTF-8 or cannot be parsed as csv, or a header that differs from the expected one.
    """
    try:
        with ZipFile(path) as outer:
            if NESTED_ARCHIVE_NAME not in outer.namelist():
                raise BatchRejectedError([f"the zip must hold {NESTED_ARCHIVE_NAME}"])
            with ZipFile(io.BytesIO(outer.read(NESTED_ARCHIVE_NAME))) as nested:
                if CSV_NAME not in nested.namelist():
                    raise BatchRejectedError([f"{NESTED_ARCHIVE_NAME} must hold {CSV_NAME}"])
                year, month, day = nested.getinfo(CSV_NAME).date_time[:3]
                content = nested.read(CSV_NAME)
        try:
            source_file_date = date(year, month, day)
        except ValueError:
            raise BatchRejectedError([f"the zip records no valid date for {CSV_NAME}"]) from None
        # `newline=""` keeps the line ends inside quoted fields as the file has them.
        reader = csv.reader(io.StringIO(content.decode("utf-8"), newline=""), strict=True)
        header_problems = _header_problems(next(reader, []))
        if header_problems:
            raise BatchRejectedError(header_problems)
        return list(reader), source_file_date
    except DAMAGED_ZIP_ERRORS as exc:
        raise BatchRejectedError([UNREADABLE_ZIP]) from exc
    except UnicodeDecodeError as exc:
        raise BatchRejectedError([f"{CSV_NAME} is not valid UTF-8 text"]) from exc
    except csv.Error as exc:
        raise BatchRejectedError([f"{CSV_NAME} cannot be parsed"]) from exc


def _header_problems(header: Sequence[str]) -> list[str]:
    if len(header) != len(EXPECTED_HEADER):
        return [
            f"row {HEADER_ROW}: header has {len(header)} columns, expected {len(EXPECTED_HEADER)}"
        ]
    return [
        f"row {HEADER_ROW}: column {index} should be {expected!r}"
        for index, (found, expected) in enumerate(zip(header, EXPECTED_HEADER, strict=True), 1)
        if found != expected
    ]


def _duplicate_problems(rows: Sequence[Sequence[str]], header: str) -> list[str]:
    """One problem per row that repeats an earlier row's value. Retired notices count too."""
    index = COLUMN[header]
    seen: Counter[str] = Counter()
    problems: list[str] = []
    for row_number, cells in enumerate(rows, start=HEADER_ROW + 1):
        if len(cells) != len(EXPECTED_HEADER):
            continue
        seen[cells[index]] += 1
        if seen[cells[index]] > 1:
            problems.append(f"row {row_number}: {header} appears more than once in the file")
    return problems


def _entry(
    cells: Sequence[str], row_number: int, source_file_date: date
) -> tuple[EvidenceCorpusEntry | None, list[str]]:
    """The document and chunks of one row, or the reasons it fails. Cell values are left out."""
    problems: list[str] = []
    document: EvidenceDocumentRow | None = None
    try:
        document = EvidenceDocumentRow.model_validate(
            {
                "source": EvidenceSource.CMS_NCD,
                "source_document_id": cells[COLUMN[ID_HEADER]],
                "section_number": cells[COLUMN[SECTION_NUMBER_HEADER]],
                "title": cells[COLUMN[TITLE_HEADER]],
                "version_number": cells[COLUMN[VERSION_HEADER]],
                "effective_date": cells[COLUMN[EFFECTIVE_DATE_HEADER]],
                "source_file_date": source_file_date,
            }
        )
    except ValidationError as exc:
        problems.extend(describe_validation_error(row_number, exc))
    ama_notice = cells[COLUMN[AMA_NOTICE_HEADER]]
    if ama_notice == SOURCE_TRUE:
        # The file's own flag for text that needs the AMA copyright notice for CPT codes.
        problems.append(
            f"row {row_number}: {AMA_NOTICE_HEADER} is True: the text may hold third-party content"
        )
    elif ama_notice != SOURCE_FALSE:
        problems.append(f"row {row_number}: {AMA_NOTICE_HEADER} must be True or False")
    sections = _sections(cells)
    if not sections:
        problems.append(f"row {row_number}: no policy text")
    elif any(
        REPLACEMENT_CHARACTER in paragraph.text
        for section in sections
        for paragraph in section.paragraphs
    ):
        problems.append(f"row {row_number}: the policy text holds an unreadable character")
    if problems or document is None:
        return None, problems
    return EvidenceCorpusEntry(document=document, chunks=tuple(chunk_document(sections))), []


def _sections(cells: Sequence[str]) -> list[SectionText]:
    """The policy columns that hold text once the markup is gone, as sections for the chunker."""
    sections: list[SectionText] = []
    for header, title in POLICY_SECTIONS:
        paragraphs = html_to_paragraphs(cells[COLUMN[header]])
        if paragraphs:
            sections.append(SectionText(title=title, paragraphs=tuple(paragraphs)))
    return sections
