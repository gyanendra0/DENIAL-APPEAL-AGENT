"""Load chapters of the CMS Internet-Only Manuals (PDF files) as evidence.

One file is one chapter. Each numbered section of the chapter (for example `50.4.1`) becomes
one document, and its text is cut into chunks (`src.rag.chunking`). A file that fails any
quality gate rejects the whole batch: nothing is returned. The sections then replace the
stored ones of the source `cms_manual` in `evidence_documents` and `evidence_chunks`, public
reference tables (no `account_id`); the coverage determinations stay as they are.

How a chapter is read:

1. The first two lines name the manual and the chapter. The manual's name gives the publication
   number (`PUBLICATIONS`).
2. The table of contents lists the numbered sections. It is read as the list of sections to
   find, then left out.
3. Each section's heading is looked for in the body, in the order of the contents. A numbered
   line alone is not a heading: the text holds many such lines that are not.
4. The line under a heading is its revision line, for example
   `(Rev. 12, Issued: 01-02-24, Effective: 03-04-24, Implementation: 03-05-24)`. It gives the
   version number (the revision number) and the effective date. With no effective date in it,
   the issued date is used. A section with no revision line gets version 1 and the file date.
5. The table "Transmittals Issued for this Chapter" at the end is left out.
6. Inside a section, a line of only whitespace ends a paragraph.

A section with no text (a parent heading, followed by its own subsections) is counted and left
out. Tables are kept as the PDF library returns them, with their columns run together.

The files have no release date that can be trusted, so the caller passes it in.
"""

import logging
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from src.db.models import EvidenceSource
from src.ingest.evidence_corpus import UNREADABLE_CHARACTERS, replace_source_documents
from src.ingest.marketplace_denials import BatchRejectedError
from src.ingest.pdf_text import read_pdf_pages
from src.rag.chunking import Chunk, Paragraph, SectionText, chunk_document

logger = logging.getLogger(__name__)

# The manual's name on the first line of a chapter, and its publication number.
PUBLICATIONS = {
    "Medicare Benefit Policy Manual": "100-02",
    "Medicare Claims Processing Manual": "100-04",
}
CHAPTER_PATTERN = re.compile(r"Chapter\s+([0-9]{1,3})\b")
CONTENTS_MARKER = "Table of Contents"
TRANSMITTALS_MARKER = "Transmittals Issued for this Chapter"
# A line that starts with a section number: `50.4.1 - Title`, with a dash of any kind or none.
NUMBERED_LINE_PATTERN = re.compile(
    r"\s*([0-9]{1,3}(?:\.[0-9]{1,3}){0,6})\.?(?:\s*[-–—‐‑]\s*|\s+)(\S.*?)?\s*"
)
DASH_AFTER_NUMBER_PATTERN = re.compile(r"\s*[0-9.]+\s*[-–—‐‑]")
REVISION_START_PATTERN = re.compile(r"\s*\(\s*Rev")
# An older revision line left at the end of a heading: `20.1 - Title (Rev. 1, 10-01-03)`.
REVISION_IN_TITLE_PATTERN = re.compile(r"\s*\(\s*Rev[^)]*\)\s*$")
REVISION_NUMBER_PATTERN = re.compile(r"Rev\s*\.?\s*:?\s*([0-9]+)")
# Month-day-year. A date wrapped over two lines has a space after a dash.
DATE = r"([0-9]{1,2})-\s?([0-9]{1,2})-\s?([0-9]{4}|[0-9]{2})(?![0-9])"
DATE_PATTERN = re.compile(DATE)
EFFECTIVE_DATE_PATTERN = re.compile(
    r"Effective(?:\s*/\s*Implementation)?(?:\s*Dates?)?\s*:?\s*" + DATE
)
# A year written with two digits is in this century: the manuals went online in 2003.
TWO_DIGIT_YEAR_BASE = 2000
# A heading wraps over at most this many lines, so its revision line is at most this many
# lines under its first line.
MAX_LINES_TO_REVISION = 3
# A revision line wraps over at most this many lines.
MAX_REVISION_LINES = 4
VERSION_WITHOUT_REVISION_LINE = 1
MAX_TITLE_CHARS = 300
# A word cut at a line end keeps its hyphen: `opt-` + `out` is `opt-out`.
HYPHEN_AT_LINE_END_PATTERN = re.compile(r"[A-Za-z]-$")


class ManualSectionRow(BaseModel):
    """One validated manual section, shaped like the `evidence_documents` table.

    `source_document_id` and `section_number` hold the same value: publication, chapter and
    section number, for example `100-04-30-150.5.2.1`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EvidenceSource
    source_document_id: str = Field(
        pattern=r"^[0-9]{3}-[0-9]{2}-[0-9]{1,3}-[0-9]{1,3}(\.[0-9]{1,3}){0,6}$", max_length=20
    )
    section_number: str = Field(
        pattern=r"^[0-9]{3}-[0-9]{2}-[0-9]{1,3}-[0-9]{1,3}(\.[0-9]{1,3}){0,6}$", max_length=20
    )
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    version_number: int = Field(ge=1)
    effective_date: date
    source_file_date: date

    @field_validator("title", mode="before")
    @classmethod
    def _single_spaces(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value


class ManualSectionEntry(BaseModel):
    """One manual section with the chunks of its text, in reading order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document: ManualSectionRow
    chunks: tuple[Chunk, ...] = Field(min_length=1)


class ManualChapterBatch(BaseModel):
    """The sections read from the files, and the counts a report needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: list[ManualSectionEntry]
    # Sections with a heading and no text: left out.
    empty_skipped: int = Field(ge=0)
    # Loaded sections that got version 1 and the file date.
    without_revision_line: int = Field(ge=0)
    # Loaded sections whose revision line has no usable effective date.
    effective_date_from_issued: int = Field(ge=0)

    @property
    def chunk_count(self) -> int:
        return sum(len(entry.chunks) for entry in self.entries)


class _Counts:
    """What `read_manual_chapters` counts while it reads."""

    def __init__(self) -> None:
        self.empty_skipped = 0
        self.without_revision_line = 0
        self.effective_date_from_issued = 0


class _Revision(BaseModel):
    """What one revision line says. `effective` is None when the line has no usable date."""

    model_config = ConfigDict(frozen=True)

    number: int
    issued: date
    effective: date | None


def read_manual_chapters(paths: Sequence[Path], *, source_file_date: date) -> ManualChapterBatch:
    """Read the manual chapter PDFs at `paths` and return their validated sections with chunks.

    `source_file_date` is the release date kept with every section. Raises
    `BatchRejectedError` if any gate fails in any file. Takes a few seconds per real chapter.
    """
    problems: list[str] = []
    entries: list[ManualSectionEntry] = []
    counts = _Counts()
    if not paths:
        problems.append("no files to read")
    for path in paths:
        try:
            pages = read_pdf_pages(path)
        except BatchRejectedError as exc:
            problems.extend(exc.problems)
            continue
        file_entries, file_problems = _chapter_entries(
            path.name, pages, source_file_date=source_file_date, counts=counts
        )
        entries.extend(file_entries)
        problems.extend(file_problems)
    seen: set[str] = set()
    for entry in entries:
        if entry.document.section_number in seen:
            problems.append(
                f"section {entry.document.section_number} appears in more than one file"
            )
        seen.add(entry.document.section_number)
    if problems:
        raise BatchRejectedError(problems)

    batch = ManualChapterBatch(
        entries=entries,
        empty_skipped=counts.empty_skipped,
        without_revision_line=counts.without_revision_line,
        effective_date_from_issued=counts.effective_date_from_issued,
    )
    logger.info(
        "read %d manual sections with %d chunks from %d files, left out %d with no text",
        len(entries),
        batch.chunk_count,
        len(paths),
        counts.empty_skipped,
    )
    return batch


def replace_manual_chapters(session: Session, batch: ManualChapterBatch) -> tuple[int, int]:
    """Make the stored manual sections exactly `batch`: remove them all, then store `batch`.

    Only rows of the source `cms_manual` are removed; their chunks and vectors go with them.
    A chapter that is not in `batch` is gone afterwards. Returns (documents, chunks) written.
    Does not commit: the caller owns the transaction, so a failure brings the old rows back.
    """
    return replace_source_documents(
        session,
        EvidenceSource.CMS_MANUAL,
        [(entry.document.model_dump(), entry.chunks) for entry in batch.entries],
    )


def _chapter_entries(
    file_name: str, pages: Sequence[str], *, source_file_date: date, counts: _Counts
) -> tuple[list[ManualSectionEntry], list[str]]:
    """The sections of one chapter, or the reasons the file fails. No manual text in a reason."""
    # A page can end in the middle of a sentence, so pages are joined like lines.
    lines = "\n".join(pages).split("\n")
    prefix = _id_prefix(lines)
    if prefix is None:
        return [], [f"{file_name}: the first lines do not name a known manual and a chapter"]
    contents_start = _index_of(lines, CONTENTS_MARKER, start=0)
    if contents_start is None:
        return [], [f"{file_name}: no table of contents"]
    first_entry = next(
        (
            index
            for index in range(contents_start + 1, len(lines))
            if _line_number(lines[index]) is not None
        ),
        None,
    )
    if first_entry is None:
        return [], [f"{file_name}: no numbered section"]
    first_number = _line_number(lines[first_entry])
    body_start = next(
        (
            index
            for index in range(first_entry + 1, len(lines))
            if _line_number(lines[index]) == first_number and _revision_follows(lines, index)
        ),
        None,
    )
    if body_start is None:
        return [], [f"{file_name}: the text of the first section was not found"]
    body_end = _index_of(lines, TRANSMITTALS_MARKER, start=body_start)
    if body_end is None:
        return [], [f"{file_name}: no transmittals table at the end of the chapter"]

    numbers: list[str] = []
    problems: list[str] = []
    for line in lines[contents_start + 1 : body_start]:
        number = _line_number(line)
        if number is None:
            continue
        if number in numbers:
            problems.append(
                f"{file_name}: section {number} appears more than once in the table of contents"
            )
        numbers.append(number)
    if problems:
        return [], problems

    body = lines[body_start:body_end]
    headings = _heading_indexes(body, numbers)
    missing = [number for number, index in zip(numbers, headings, strict=True) if index is None]
    if missing:
        return [], [
            f"{file_name}: section {number} is in the table of contents but its heading was "
            "not found in the text"
            for number in missing
        ]
    starts = [index for index in headings if index is not None]
    entries: list[ManualSectionEntry] = []
    for position, (number, start) in enumerate(zip(numbers, starts, strict=True)):
        end = starts[position + 1] if position + 1 < len(starts) else len(body)
        entry, section_problems = _section_entry(
            f"{prefix}-{number}", body[start:end], source_file_date=source_file_date, counts=counts
        )
        problems.extend(f"{file_name}: section {number}: {problem}" for problem in section_problems)
        if entry is not None:
            entries.append(entry)
    if not entries and not problems:
        problems.append(f"{file_name}: no section holds any text")
    return entries, problems


def _id_prefix(lines: Sequence[str]) -> str | None:
    """Publication and chapter, like `100-02-15`, from the first two lines that hold text."""
    head = [line.strip() for line in lines if line.strip()][:2]
    if len(head) < 2 or head[0] not in PUBLICATIONS:
        return None
    chapter = CHAPTER_PATTERN.match(head[1])
    if chapter is None:
        return None
    return f"{PUBLICATIONS[head[0]]}-{int(chapter.group(1))}"


def _index_of(lines: Sequence[str], marker: str, *, start: int) -> int | None:
    return next(
        (index for index in range(start, len(lines)) if lines[index].strip() == marker), None
    )


def _line_number(line: str) -> str | None:
    """The section number a line starts with, or None."""
    match = NUMBERED_LINE_PATTERN.fullmatch(line)
    return match.group(1) if match else None


def _revision_follows(lines: Sequence[str], index: int) -> bool:
    """Whether a revision line comes under line `index` before any blank line."""
    return _revision_index(lines, index) is not None


def _revision_index(lines: Sequence[str], index: int) -> int | None:
    for candidate in range(index + 1, min(index + 1 + MAX_LINES_TO_REVISION, len(lines))):
        if not lines[candidate].strip():
            return None
        if REVISION_START_PATTERN.match(lines[candidate]):
            return candidate
    return None


def _heading_indexes(body: Sequence[str], numbers: Sequence[str]) -> list[int | None]:
    """Where each section's heading is in `body`, in the order of `numbers`; None if not found.

    A heading is a line that starts with the section's number and has a revision line under
    it. Only when no such line is left is a line with the number, a dash and a blank line
    before it taken: a heading with no revision line.
    """
    found: list[int | None] = []
    start = 0
    for number in numbers:
        candidates = [
            index for index in range(start, len(body)) if _line_number(body[index]) == number
        ]
        index = next((c for c in candidates if _revision_follows(body, c)), None)
        if index is None:
            index = next(
                (
                    c
                    for c in candidates
                    if c > 0
                    and not body[c - 1].strip()
                    and DASH_AFTER_NUMBER_PATTERN.match(body[c])
                ),
                None,
            )
        found.append(index)
        if index is not None:
            start = index + 1
    return found


def _section_entry(
    section_id: str, lines: Sequence[str], *, source_file_date: date, counts: _Counts
) -> tuple[ManualSectionEntry | None, list[str]]:
    """The document and chunks of one section, whose heading is `lines[0]`.

    Returns no entry and no problem for a section with no text.
    """
    problems: list[str] = []
    revision_start = _revision_index(lines, 0)
    revision: _Revision | None = None
    if revision_start is None:
        # The heading runs to the first blank line.
        title_end = next(
            (index for index in range(1, len(lines)) if not lines[index].strip()), len(lines)
        )
        text_start = title_end
    else:
        title_end = revision_start
        revision_end = _revision_end(lines, revision_start)
        if revision_end is None:
            problems.append(f"the revision line does not end within {MAX_REVISION_LINES} lines")
            revision_end = revision_start + 1
        text_start = revision_end
        revision = _parse_revision(" ".join(lines[revision_start:revision_end]))
        if revision is None:
            problems.append("the revision line has no revision number or no issued date")
    match = NUMBERED_LINE_PATTERN.fullmatch(lines[0])
    title_parts = [(match.group(2) or "") if match else "", *lines[1:title_end]]
    title = REVISION_IN_TITLE_PATTERN.sub("", " ".join(" ".join(title_parts).split()))
    paragraphs = _paragraphs(lines[text_start:])
    if _has_unreadable_character(title) or any(
        _has_unreadable_character(paragraph.text) for paragraph in paragraphs
    ):
        problems.append("the text holds an unreadable character")
    if problems:
        return None, problems
    if not paragraphs:
        counts.empty_skipped += 1
        return None, []

    if revision is None:
        version_number, effective_date = VERSION_WITHOUT_REVISION_LINE, source_file_date
    else:
        version_number, effective_date = revision.number, revision.effective or revision.issued
    try:
        document = ManualSectionRow(
            source=EvidenceSource.CMS_MANUAL,
            source_document_id=section_id,
            section_number=section_id,
            title=title[:MAX_TITLE_CHARS].rstrip(),
            version_number=version_number,
            effective_date=effective_date,
            source_file_date=source_file_date,
        )
    except ValidationError as exc:
        # The value is left out on purpose: it is text of the file.
        return None, [
            f"{'.'.join(str(part) for part in error['loc']) or 'row'}: {error['msg']}"
            for error in exc.errors(include_input=False)
        ]
    if revision_start is None:
        counts.without_revision_line += 1
    elif revision is not None and revision.effective is None:
        counts.effective_date_from_issued += 1
    chunks = chunk_document([SectionText(title=None, paragraphs=tuple(paragraphs))])
    return ManualSectionEntry(document=document, chunks=tuple(chunks)), []


def _revision_end(lines: Sequence[str], start: int) -> int | None:
    """The index after the revision line that starts at `start`, or None if it does not end.

    The line ends where its brackets close: it can hold brackets of its own.
    """
    depth = 0
    for index in range(start, min(start + MAX_REVISION_LINES, len(lines))):
        depth += lines[index].count("(") - lines[index].count(")")
        if depth <= 0:
            return index + 1
    return None


def _parse_revision(text: str) -> _Revision | None:
    """Read a revision line field by field; None without a revision number and an issued date."""
    number = REVISION_NUMBER_PATTERN.search(text)
    if number is None or int(number.group(1)) < 1:
        return None
    issued_match = DATE_PATTERN.search(text, number.end())
    issued = _to_date(issued_match)
    if issued is None:
        return None
    return _Revision(
        number=int(number.group(1)),
        issued=issued,
        effective=_to_date(EFFECTIVE_DATE_PATTERN.search(text)),
    )


def _to_date(match: re.Match[str] | None) -> date | None:
    if match is None:
        return None
    month, day, year = (int(part) for part in match.groups())
    if year < 100:
        year += TWO_DIGIT_YEAR_BASE
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _paragraphs(lines: Sequence[str]) -> list[Paragraph]:
    """Runs of lines that hold text, each joined into one paragraph with single spaces."""
    paragraphs: list[Paragraph] = []
    current = ""
    for line in [*lines, ""]:
        text = " ".join(line.split())
        if not text:
            if current:
                paragraphs.append(Paragraph(text=current))
            current = ""
        elif HYPHEN_AT_LINE_END_PATTERN.search(current) and text[0].islower():
            current += text
        else:
            current = f"{current} {text}" if current else text
    return paragraphs


def _has_unreadable_character(text: str) -> bool:
    return any(character in text for character in UNREADABLE_CHARACTERS)
