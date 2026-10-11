"""Manual chapter reader tests. The fixture is a made-up chapter 99 of two pages.

Its table of contents lists five sections. Section 10 runs over the page end and holds a word
cut at a line end and a numbered line that is not a heading. Section 10.1 has a heading and a
revision line that both wrap, and no blank line before its heading. Section 20 has no text.
Section 20.1 has a revision line with no effective date. Section 30 has no revision line.
"""

import hashlib
import logging
from datetime import date
from pathlib import Path

import pytest

from src.db.models import EvidenceSource
from src.ingest.manual_chapters import (
    MAX_TITLE_CHARS,
    ManualChapterBatch,
    ManualSectionEntry,
    read_manual_chapters,
)
from src.ingest.marketplace_denials import BatchRejectedError
from src.ingest.pdf_text import read_pdf_pages
from src.rag.chunking import CHUNKER_VERSION, MAX_CHUNK_CHARS
from tests.ingest.helpers import MANUAL_FIXTURE, manual_pdf, write_text_pdf

FILE_DATE = date(2026, 10, 11)
MANUAL_LINE = "    Medicare Benefit Policy Manual"
CHAPTER_LINE = "Chapter 99 - Made-Up Example Services"
FIRST_HEADING = "10 - Example Coverage Rules"
FIRST_REVISION = "(Rev. 12, Issued: 01-02-24, Effective: 03-04-24, Implementation: 03-05-24)"
SHORT_REVISION_HEADING = "20.1 - Example With a Short Revision Line"
NO_REVISION_HEADING = "30 - Section Without a Revision Line"
NEVER_LOADED_MARKERS = (
    "Table of Contents",
    "Transmittals for Chapter 99",
    "Transmittals Issued for this Chapter",
    "TRANSMITTAL-TABLE-MARKER",
    "Rev. 500",
    "Made-Up Example Services",
    "Parent Heading Only",
)


def _read(*paths: Path) -> ManualChapterBatch:
    return read_manual_chapters(list(paths), source_file_date=FILE_DATE)


def _rejected(*paths: Path) -> list[str]:
    with pytest.raises(BatchRejectedError) as excinfo:
        _read(*paths)
    return excinfo.value.problems


def _by_number(batch: ManualChapterBatch) -> dict[str, ManualSectionEntry]:
    return {entry.document.section_number: entry for entry in batch.entries}


def _text(entry: ManualSectionEntry) -> str:
    return "\n\n".join(chunk.text for chunk in entry.chunks)


def test_gives_one_document_per_numbered_section_with_text() -> None:
    batch = _read(MANUAL_FIXTURE)
    assert [(entry.document.section_number, entry.document.title) for entry in batch.entries] == [
        ("100-02-99-10", "Example Coverage Rules"),
        ("100-02-99-10.1", "A Longer Example Title That Wraps onto a Second Line"),
        ("100-02-99-20.1", "Example With a Short Revision Line"),
        ("100-02-99-30", "Section Without a Revision Line"),
    ]


def test_the_document_id_is_the_section_number_and_the_source_is_the_manuals() -> None:
    for entry in _read(MANUAL_FIXTURE).entries:
        assert entry.document.source_document_id == entry.document.section_number
        assert entry.document.source is EvidenceSource.CMS_MANUAL
        assert entry.document.source_file_date == FILE_DATE


def test_a_whitespace_only_line_ends_a_paragraph() -> None:
    assert _text(_by_number(_read(MANUAL_FIXTURE))["100-02-99-10"]).split("\n\n") == [
        "The fixture clinic covers a made-up service when both example conditions are met. "
        "This paragraph runs over the end of the page and stops in the middle of a sentence.",
        "A. Example Sub-Heading",
        "The example patient may opt-out of the made-up plan after the 30 - day example "
        "period, or after a pre- Example review.",
    ]


def test_a_numbered_line_with_no_revision_line_under_it_is_not_a_heading() -> None:
    batch = _by_number(_read(MANUAL_FIXTURE))
    assert "30 - day example period" in _text(batch["100-02-99-10"])
    assert batch["100-02-99-30"].document.title == "Section Without a Revision Line"


def test_a_heading_with_no_blank_line_before_it_starts_a_section() -> None:
    batch = _by_number(_read(MANUAL_FIXTURE))
    assert _text(batch["100-02-99-10"]).endswith("Example review.")
    assert _text(batch["100-02-99-10.1"]) == (
        "B3-9999\n\n• The made-up supplier’s example bullet (§10)."
    )


def test_the_contents_the_transmittals_table_and_the_front_lines_are_in_no_chunk() -> None:
    batch = _read(MANUAL_FIXTURE)
    loaded = "\n".join(
        [entry.document.title for entry in batch.entries]
        + [chunk.text for entry in batch.entries for chunk in entry.chunks]
    )
    for marker in NEVER_LOADED_MARKERS:
        assert marker not in loaded
    # Each title is in the contents and in the body; its section holds it once, as its title.
    assert loaded.count("Example Coverage Rules") == 1


def test_a_chunk_has_no_section_title_and_the_hash_of_its_text() -> None:
    for entry in _read(MANUAL_FIXTURE).entries:
        assert [chunk.chunk_index for chunk in entry.chunks] == list(range(len(entry.chunks)))
        for chunk in entry.chunks:
            assert chunk.section_title is None
            assert chunk.chunker_version == CHUNKER_VERSION
            assert chunk.text_sha256 == hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()


def test_version_and_effective_date_come_from_the_revision_line() -> None:
    document = _by_number(_read(MANUAL_FIXTURE))["100-02-99-10"].document
    assert (document.version_number, document.effective_date) == (12, date(2024, 3, 4))


def test_reads_a_revision_line_that_wraps_inside_a_date() -> None:
    document = _by_number(_read(MANUAL_FIXTURE))["100-02-99-10.1"].document
    assert (document.version_number, document.effective_date) == (34, date(2024, 7, 8))


def test_a_revision_line_with_no_effective_date_gives_the_issued_date() -> None:
    batch = _read(MANUAL_FIXTURE)
    document = _by_number(batch)["100-02-99-20.1"].document
    assert (document.version_number, document.effective_date) == (7, date(2005, 11, 12))
    assert batch.effective_date_from_issued == 1


def test_a_section_with_no_revision_line_gets_version_1_and_the_file_date() -> None:
    batch = _read(MANUAL_FIXTURE)
    document = _by_number(batch)["100-02-99-30"].document
    assert (document.version_number, document.effective_date) == (1, FILE_DATE)
    assert batch.without_revision_line == 1


def test_a_section_with_no_text_is_counted_and_left_out() -> None:
    batch = _read(MANUAL_FIXTURE)
    assert "100-02-99-20" not in _by_number(batch)
    assert batch.empty_skipped == 1
    assert batch.chunk_count == 4


@pytest.mark.parametrize(
    ("line", "version", "effective"),
    [
        ("(Rev. 8; Issued: 01-02-24; Effective: 03-04-24; Implementation: 03-05-24)", 8, (3, 4)),
        ("(Rev.: 8; Issued: 01-02-24; Effective:03-04-24; Implementation:03-05-24)", 8, (3, 4)),
        ("(Rev .8, Issued: 01-02-24, Effective: 03-04-24, Implementation: 03-05-24)", 8, (3, 4)),
        ("(Rev. 8., Issued: 01-02-24, Effective: 03-04-24, Implementation: 03-05-24)", 8, (3, 4)),
        ("(Rev. 8 Issued: 01-02-24, Effective: 03-04-24, Implementation: 03-05-24)", 8, (3, 4)),
        ("(Rev. 8, Issued:01-02-24 Effective:03-04-24 Implementation:03-05-24)", 8, (3, 4)),
        ("(Rev. 8, Issued: 01-02-24: Effective: 03-04-24, Implementation: 03-05-24)", 8, (3, 4)),
        (
            "(Rev. 8, Issued: 01-02-2024, Effective: 03-04-2024, Implementation: 3-5-2024)",
            8,
            (3, 4),
        ),
        ("(Rev. 8, Issued: 01-02-24, Effective/Implementation Date: 03-04-24)", 8, (3, 4)),
        ("(Rev. 8, Issued: 01-02-24, Effective/Implementation Dates: 03-04-24)", 8, (3, 4)),
        ("(Rev. 8, Issued: 01-02-24, Effective Date: 03-04-24)", 8, (3, 4)),
        ("(Rev. 8, Issued: 01-02-24, Effective: 03- 04- 24, Implementation: 03-05-24)", 8, (3, 4)),
        ("(Rev. 8, Issued: 01-02-24, Effective: 03-04-24, Effective: 06-07-24)", 8, (3, 4)),
        (
            "(Rev. 8, Issued: 01-02-24, Effective: 03-04-24, Example Visit (EV), Impl: 1-1-25)",
            8,
            (3, 4),
        ),
        ("(Rev. 8, Issued: 01-02-24, Effective: N/A, Implementation: 03-05-24)", 8, (1, 2)),
        ("(Rev. 8, Issued: 01-02-24, Effective: 03-04 24, Implementation: 03-05-24)", 8, (1, 2)),
        (
            "(Rev. 8, Issued: 01-02-24, Effective: Upon Start of EX-10, Implementation: 3-5-24)",
            8,
            (1, 2),
        ),
        ("(Rev. 8, Issued: 01-02-24, Effective: 13-40-24, Implementation: 03-05-24)", 8, (1, 2)),
        ("(Rev. 8, Issued: 01-02-24, Implementation; 03-05-24)", 8, (1, 2)),
        ("(Rev. 8, 01-02-24)", 8, (1, 2)),
    ],
)
def test_reads_every_shape_of_revision_line(
    tmp_path: Path, line: str, version: int, effective: tuple[int, int]
) -> None:
    batch = _read(manual_pdf(tmp_path, {FIRST_REVISION: line}))
    document = _by_number(batch)["100-02-99-10"].document
    assert document.version_number == version
    assert document.effective_date == date(2024, *effective)
    assert _text(_by_number(batch)["100-02-99-10"]).startswith("The fixture clinic covers")


def test_a_two_digit_year_is_in_this_century(tmp_path: Path) -> None:
    batch = _read(manual_pdf(tmp_path, {FIRST_REVISION: "(Rev. 1, 10-01-03)"}))
    assert _by_number(batch)["100-02-99-10"].document.effective_date == date(2003, 10, 1)


def test_an_old_revision_line_inside_a_heading_is_not_in_the_title(tmp_path: Path) -> None:
    path = manual_pdf(tmp_path, {FIRST_HEADING: f"{FIRST_HEADING} (Rev. 1, 10-01-03)"})
    document = _by_number(_read(path))["100-02-99-10"].document
    assert document.title == "Example Coverage Rules"
    assert document.version_number == 12


def test_a_heading_with_no_dash_is_found(tmp_path: Path) -> None:
    path = manual_pdf(tmp_path, {SHORT_REVISION_HEADING: "20.1 Example With No Dash"})
    assert _by_number(_read(path))["100-02-99-20.1"].document.title == "Example With No Dash"


def test_a_title_over_the_limit_is_cut(tmp_path: Path) -> None:
    long_title = " ".join(["Example"] * 60)
    path = manual_pdf(tmp_path, {NO_REVISION_HEADING: f"30 - {long_title}"})
    title = _by_number(_read(path))["100-02-99-30"].document.title
    assert len(title) <= MAX_TITLE_CHARS
    assert long_title.startswith(title)


def test_a_long_paragraph_is_cut_into_chunks_within_the_limit(tmp_path: Path) -> None:
    sentence = "The made-up clinic keeps an example record for every example visit."
    path = manual_pdf(
        tmp_path, {"This section has a heading and no revision line.": " ".join([sentence] * 40)}
    )
    chunks = _by_number(_read(path))["100-02-99-30"].chunks
    assert len(chunks) > 1
    assert all(len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert " ".join(chunk.text for chunk in chunks) == " ".join([sentence] * 40)


def test_the_claims_processing_manual_gets_its_own_publication_number(tmp_path: Path) -> None:
    path = manual_pdf(tmp_path, {MANUAL_LINE: "Medicare Claims Processing Manual"})
    assert _read(path).entries[0].document.section_number == "100-04-99-10"


def test_reads_several_chapters_in_the_order_given(tmp_path: Path) -> None:
    other = manual_pdf(tmp_path, {CHAPTER_LINE: "Chapter 7 – Another Made-Up Chapter"})
    batch = _read(other, MANUAL_FIXTURE)
    assert [entry.document.section_number for entry in batch.entries] == [
        "100-02-7-10",
        "100-02-7-10.1",
        "100-02-7-20.1",
        "100-02-7-30",
        "100-02-99-10",
        "100-02-99-10.1",
        "100-02-99-20.1",
        "100-02-99-30",
    ]
    assert (batch.empty_skipped, batch.without_revision_line, batch.chunk_count) == (2, 2, 8)


def test_the_same_file_always_gives_the_same_batch() -> None:
    assert _read(MANUAL_FIXTURE) == _read(MANUAL_FIXTURE)


def test_logs_the_counts(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="src.ingest.manual_chapters"):
        _read(MANUAL_FIXTURE)
    assert (
        "read 4 manual sections with 4 chunks from 1 files, left out 1 with no text" in caplog.text
    )


def test_rejects_an_empty_list_of_files() -> None:
    assert _rejected() == ["no files to read"]


def test_rejects_a_file_that_is_not_a_pdf(tmp_path: Path) -> None:
    path = tmp_path / "notes.pdf"
    path.write_bytes(b"made-up bytes, not a pdf")
    assert _rejected(path) == ["notes.pdf: not a readable PDF file"]


def test_rejects_a_pdf_with_no_text(tmp_path: Path) -> None:
    path = write_text_pdf(tmp_path / "scan.pdf", [[], []])
    assert _rejected(path) == ["scan.pdf: the PDF holds no text that can be extracted"]


def test_one_bad_file_rejects_the_good_one_too(tmp_path: Path) -> None:
    path = tmp_path / "notes.pdf"
    path.write_bytes(b"made-up bytes, not a pdf")
    assert _rejected(MANUAL_FIXTURE, path) == ["notes.pdf: not a readable PDF file"]


@pytest.mark.parametrize(
    "replace",
    [
        {MANUAL_LINE: "Made-Up Manual of Examples"},
        {MANUAL_LINE: None},
        {CHAPTER_LINE: "Part 99 - Made-Up Example Services"},
        {CHAPTER_LINE: "Chapter Nine - Made-Up Example Services"},
    ],
)
def test_rejects_a_file_whose_first_lines_name_no_known_manual_and_chapter(
    tmp_path: Path, replace: dict[str, str | None]
) -> None:
    assert _rejected(manual_pdf(tmp_path, replace)) == [
        "chapter.pdf: the first lines do not name a known manual and a chapter"
    ]


def test_rejects_a_file_with_no_table_of_contents(tmp_path: Path) -> None:
    path = manual_pdf(tmp_path, {"Table of Contents": None})
    assert _rejected(path) == ["chapter.pdf: no table of contents"]


def test_rejects_a_file_with_no_numbered_section(tmp_path: Path) -> None:
    path = write_text_pdf(
        tmp_path / "plain.pdf",
        [[MANUAL_LINE, CHAPTER_LINE, "", "Table of Contents", "", "Made-up text, no sections."]],
    )
    assert _rejected(path) == ["plain.pdf: no numbered section"]


def test_rejects_a_file_with_a_contents_list_and_no_body(tmp_path: Path) -> None:
    path = write_text_pdf(
        tmp_path / "list.pdf",
        [[MANUAL_LINE, CHAPTER_LINE, "", "Table of Contents", "", "10 - Example", "20 - Other"]],
    )
    assert _rejected(path) == ["list.pdf: the text of the first section was not found"]


def test_rejects_a_file_with_no_transmittals_table(tmp_path: Path) -> None:
    path = manual_pdf(tmp_path, {"Transmittals Issued for this Chapter": None})
    assert _rejected(path) == ["chapter.pdf: no transmittals table at the end of the chapter"]


def test_rejects_a_section_number_listed_twice_in_the_contents(tmp_path: Path) -> None:
    path = manual_pdf(tmp_path, {" 20 – Parent Heading Only": "10.1 - Parent Heading Only"})
    assert _rejected(path) == [
        "chapter.pdf: section 10.1 appears more than once in the table of contents"
    ]


def test_rejects_a_contents_entry_with_no_heading_in_the_text(tmp_path: Path) -> None:
    path = manual_pdf(tmp_path, {" 20 – Parent Heading Only": "15 - Listed and Never Written"})
    assert _rejected(path) == [
        "chapter.pdf: section 15 is in the table of contents but its heading was not found "
        "in the text"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "(Rev. Issued: 01-02-24, Effective: 03-04-24)",
        "(Rev. 0, Issued: 01-02-24, Effective: 03-04-24)",
        "(Rev. 12, Issued: REVISION-LINE-MARKER)",
        "(Rev. 12, Issued: 13-40-24, Effective: REVISION-LINE-MARKER)",
    ],
)
def test_rejects_a_revision_line_with_no_number_or_no_issued_date(
    tmp_path: Path, line: str
) -> None:
    problems = _rejected(manual_pdf(tmp_path, {FIRST_REVISION: line}))
    assert problems == [
        "chapter.pdf: section 10: the revision line has no revision number or no issued date"
    ]


def test_rejects_a_revision_line_whose_bracket_never_closes(tmp_path: Path) -> None:
    line = "(Rev. 12, Issued: 01-02-24, Effective: 03-04-24, Example (EX: REVISION-LINE-MARKER"
    assert _rejected(manual_pdf(tmp_path, {FIRST_REVISION: line})) == [
        "chapter.pdf: section 10: the revision line does not end within 4 lines"
    ]


def test_rejects_an_id_longer_than_the_column(tmp_path: Path) -> None:
    path = manual_pdf(tmp_path, {SHORT_REVISION_HEADING: "200.10.10.1 - Example Deep Section"})
    problems = _rejected(path)
    assert len(problems) == 2
    assert all(
        problem.startswith("chapter.pdf: section 200.10.10.1: s") and "at most 20" in problem
        for problem in problems
    )


def test_rejects_a_heading_with_a_number_and_no_title(tmp_path: Path) -> None:
    problems = _rejected(manual_pdf(tmp_path, {SHORT_REVISION_HEADING: "20.1 -"}))
    assert problems == ["chapter.pdf: section 20.1: title: String should have at least 1 character"]


def test_rejects_the_same_chapter_given_twice(tmp_path: Path) -> None:
    problems = _rejected(MANUAL_FIXTURE, manual_pdf(tmp_path))
    assert problems == [
        "section 100-02-99-10 appears in more than one file",
        "section 100-02-99-10.1 appears in more than one file",
        "section 100-02-99-20.1 appears in more than one file",
        "section 100-02-99-30 appears in more than one file",
    ]


def test_rejects_a_chapter_whose_sections_all_have_no_text(tmp_path: Path) -> None:
    path = write_text_pdf(
        tmp_path / "bare.pdf",
        [
            [
                "Medicare Claims Processing Manual",
                "Chapter 98 - Made-Up Headings",
                "Table of Contents",
                "10 - Example Parent",
                "",
                "10 - Example Parent",
                "(Rev. 1, 10-01-03)",
                "",
                "Transmittals Issued for this Chapter",
            ]
        ],
    )
    assert _rejected(path) == ["bare.pdf: no section holds any text"]


def test_a_problem_never_quotes_the_text_of_the_file(tmp_path: Path) -> None:
    path = manual_pdf(
        tmp_path,
        {
            FIRST_REVISION: "(Rev. 12, Issued: REVISION-LINE-MARKER)",
            SHORT_REVISION_HEADING: "200.10.10.1 - TITLE-MARKER",
        },
    )
    problems = _rejected(path)
    assert len(problems) == 3
    assert not any("MARKER" in problem for problem in problems)


def test_a_missing_file_is_not_a_rejected_batch(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _read(tmp_path / "missing.pdf")


def test_a_revision_line_far_under_a_numbered_line_does_not_make_it_a_heading(
    tmp_path: Path,
) -> None:
    path = write_text_pdf(
        tmp_path / "far.pdf",
        [
            [
                MANUAL_LINE,
                CHAPTER_LINE,
                "Table of Contents",
                "10 - Example Parent",
                "20 - Example Child",
                "",
                "10 - Example Parent",
                "(Rev. 3, 01-02-24)",
                "",
                "The made-up list names",
                "20 - example days, then",
                "three more lines of",
                "plain and ordinary",
                "made-up text before the",
                "(Rev. 9, 05-06-24) example mark.",
                "",
                "20 - Example Child",
                "(Rev. 4, 01-02-24)",
                "",
                "The text of the child section.",
                "",
                "Transmittals Issued for this Chapter",
            ]
        ],
    )
    batch = _by_number(_read(path))
    assert "20 - example days" in _text(batch["100-02-99-10"])
    assert batch["100-02-99-20"].document.version_number == 4
    assert _text(batch["100-02-99-20"]) == "The text of the child section."


@pytest.mark.parametrize("character", ["\x00", "\ufffd"])
def test_rejects_text_with_an_unreadable_character(
    monkeypatch: pytest.MonkeyPatch, character: str
) -> None:
    pages = read_pdf_pages(MANUAL_FIXTURE)
    pages[1] = pages[1].replace("bullet", f"bul{character}let")
    monkeypatch.setattr("src.ingest.manual_chapters.read_pdf_pages", lambda path: pages)
    assert _rejected(MANUAL_FIXTURE) == [
        "manual_chapter_sample.pdf: section 10.1: the text holds an unreadable character"
    ]
