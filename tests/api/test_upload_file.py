"""Reading an uploaded file. Every file here is made up; PDFs are written by `write_text_pdf`."""

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from src.api.upload_file import (
    EMPTY_FILE,
    NO_TEXT,
    NOT_UTF8,
    NUL_CHARACTER,
    PDF_UNREADABLE,
    UploadFileError,
    read_uploaded_letter,
)
from src.db.models import UploadFileType
from tests.ingest.helpers import MANUAL_FIXTURE, write_text_pdf

LETTER = "Made-up denial letter for Zebulon Quillfeather.\nClaim 999.\n"


def _refused(data: bytes) -> str:
    with pytest.raises(UploadFileError) as caught:
        read_uploaded_letter(data)
    return str(caught.value)


def test_reads_utf8_text_as_is() -> None:
    assert read_uploaded_letter(LETTER.encode("utf-8")) == (UploadFileType.TEXT, LETTER)


def test_drops_a_utf8_byte_order_mark() -> None:
    data = "﻿".encode() + LETTER.encode("utf-8")
    assert read_uploaded_letter(data) == (UploadFileType.TEXT, LETTER)


def test_keeps_characters_outside_ascii() -> None:
    text = "Denied: the supplier’s rule (§10) – “quoted”"
    assert read_uploaded_letter(text.encode("utf-8")) == (UploadFileType.TEXT, text)


def test_reads_a_pdf_and_joins_its_pages_with_a_blank_line(tmp_path: Path) -> None:
    data = write_text_pdf(tmp_path / "letter.pdf", [["first page"], ["second page"]]).read_bytes()
    assert read_uploaded_letter(data) == (UploadFileType.PDF, "first page \n\nsecond page ")


def test_the_type_comes_from_the_bytes_not_a_name() -> None:
    # Text that only mentions a PDF is still text.
    assert read_uploaded_letter(b"PDF-1.4 is named here")[0] is UploadFileType.TEXT


def test_refuses_an_empty_file() -> None:
    assert _refused(b"") == EMPTY_FILE


@pytest.mark.parametrize("data", [b"   \n\t ", "﻿  ".encode()])
def test_refuses_text_that_is_only_whitespace(data: bytes) -> None:
    assert _refused(data) == NO_TEXT


def test_refuses_bytes_that_are_not_utf8() -> None:
    assert _refused(b"Made-up letter \xff\xfe in another encoding") == NOT_UTF8


def test_refuses_a_nul_character() -> None:
    assert _refused(b"Made-up letter\x00with a NUL") == NUL_CHARACTER


@pytest.mark.parametrize("data", [b"%PDF-1.4 but nothing else", b"%PDF-"])
def test_refuses_a_damaged_pdf(data: bytes) -> None:
    assert _refused(data) == PDF_UNREADABLE


def test_refuses_a_pdf_with_no_text(tmp_path: Path) -> None:
    data = write_text_pdf(tmp_path / "scan.pdf", [[]]).read_bytes()
    assert _refused(data) == PDF_UNREADABLE


def test_refuses_an_encrypted_pdf(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=PdfReader(MANUAL_FIXTURE))
    writer.encrypt("made-up-password")
    path = tmp_path / "locked.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    assert _refused(path.read_bytes()) == PDF_UNREADABLE


def test_a_refusal_never_carries_the_file_text() -> None:
    message = _refused(b"Zebulon Quillfeather \xff")
    assert "Zebulon" not in message
