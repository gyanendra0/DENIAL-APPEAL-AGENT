"""PDF text reader tests. Every PDF here is made up and written by `write_text_pdf`."""

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from src.ingest.marketplace_denials import BatchRejectedError
from src.ingest.pdf_text import read_pdf_pages
from tests.ingest.helpers import MANUAL_FIXTURE, MANUAL_PAGES, write_text_pdf


def _rejected(path: Path) -> list[str]:
    with pytest.raises(BatchRejectedError) as excinfo:
        read_pdf_pages(path)
    return excinfo.value.problems


def test_returns_one_text_per_page_in_page_order(tmp_path: Path) -> None:
    path = write_text_pdf(tmp_path / "two.pdf", [["first page"], ["second page", "its end"]])
    assert read_pdf_pages(path) == ["first page ", "second page \nits end "]


def test_an_empty_line_comes_back_as_a_line_of_one_space(tmp_path: Path) -> None:
    path = write_text_pdf(tmp_path / "blank.pdf", [["above", "", "below"]])
    assert read_pdf_pages(path) == ["above \n \nbelow "]


def test_keeps_the_characters_outside_ascii(tmp_path: Path) -> None:
    path = write_text_pdf(tmp_path / "marks.pdf", [["• the supplier’s rule (§10) – “quoted”"]])
    assert read_pdf_pages(path) == ["• the supplier’s rule (§10) – “quoted” "]


def test_a_page_with_no_text_is_an_empty_string(tmp_path: Path) -> None:
    path = write_text_pdf(tmp_path / "gap.pdf", [["text"], []])
    assert read_pdf_pages(path) == ["text ", ""]


def test_the_committed_fixture_holds_the_lines_of_the_helper(tmp_path: Path) -> None:
    rebuilt = write_text_pdf(tmp_path / "rebuilt.pdf", MANUAL_PAGES)
    assert read_pdf_pages(MANUAL_FIXTURE) == read_pdf_pages(rebuilt)
    assert len(read_pdf_pages(MANUAL_FIXTURE)) == len(MANUAL_PAGES)


@pytest.mark.parametrize(
    "content", [b"", b"not a pdf at all", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"]
)
def test_rejects_a_file_that_is_not_a_readable_pdf(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(content)
    assert _rejected(path) == ["bad.pdf: not a readable PDF file"]


def test_rejects_a_pdf_cut_off_in_the_middle(tmp_path: Path) -> None:
    whole = write_text_pdf(tmp_path / "whole.pdf", [["some text"]]).read_bytes()
    path = tmp_path / "cut.pdf"
    path.write_bytes(whole[: len(whole) // 2])
    assert _rejected(path) == ["cut.pdf: not a readable PDF file"]


def test_rejects_a_damaged_pdf_the_library_fails_on_with_a_plain_error(tmp_path: Path) -> None:
    # Without its line end, the library reads `startxref` and the number as one bad number.
    whole = write_text_pdf(tmp_path / "whole.pdf", [["some text"]]).read_bytes()
    assert whole.count(b"startxref\n") == 1
    path = tmp_path / "damaged.pdf"
    path.write_bytes(whole.replace(b"startxref\n", b"startxrefA"))
    assert _rejected(path) == ["damaged.pdf: not a readable PDF file"]


def test_rejects_a_path_that_cannot_be_opened_as_a_file(tmp_path: Path) -> None:
    path = tmp_path / "folder.pdf"
    path.mkdir()
    assert _rejected(path) == ["folder.pdf: the file cannot be opened"]


@pytest.mark.parametrize("pages", [[[]], [[], []], [[""], ["", ""]]])
def test_rejects_a_pdf_with_no_text(tmp_path: Path, pages: list[list[str]]) -> None:
    path = write_text_pdf(tmp_path / "scan.pdf", pages)
    assert _rejected(path) == ["scan.pdf: the PDF holds no text that can be extracted"]


def test_rejects_a_pdf_with_no_pages(tmp_path: Path) -> None:
    path = write_text_pdf(tmp_path / "none.pdf", [])
    assert _rejected(path) == ["none.pdf: the PDF holds no text that can be extracted"]


def test_rejects_an_encrypted_pdf(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=PdfReader(MANUAL_FIXTURE))
    writer.encrypt("made-up-password")
    path = tmp_path / "locked.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    assert _rejected(path) == ["locked.pdf: the PDF is encrypted"]


def test_a_missing_file_is_not_a_rejected_batch(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_pdf_pages(tmp_path / "missing.pdf")
