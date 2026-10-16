"""Turn the bytes of an uploaded file into the text of one denial letter.

The type is decided from the file's first bytes, never from its name or the browser's content
type: a file that starts with `%PDF-` is read as a PDF, any other file as UTF-8 text. A
refusal carries a fixed message, never the file's name or any of its text.
"""

from src.db.models import UploadFileType
from src.ingest.marketplace_denials import BatchRejectedError
from src.ingest.pdf_text import read_pdf_bytes

PDF_MAGIC = b"%PDF-"
UTF8_BOM = "﻿"

EMPTY_FILE = "the file is empty"
NOT_UTF8 = "the file is neither a PDF nor UTF-8 text"
PDF_UNREADABLE = "the PDF cannot be read, is encrypted or holds no text (a scan is not read)"
NO_TEXT = "the file holds no text"
NUL_CHARACTER = "the file holds a NUL character"


class UploadFileError(ValueError):
    """The file cannot be read as a letter. Its text is one of the fixed messages above."""


def read_uploaded_letter(data: bytes) -> tuple[UploadFileType, str]:
    """Return the detected type and the text of an uploaded file, or raise `UploadFileError`.

    A PDF's pages are joined with a blank line. A UTF-8 byte order mark is dropped. Text that
    is only whitespace, or holds a NUL character (the database refuses one), is refused.
    """
    if not data:
        raise UploadFileError(EMPTY_FILE)
    if data.startswith(PDF_MAGIC):
        file_type = UploadFileType.PDF
        try:
            text = "\n\n".join(read_pdf_bytes(data, "upload"))
        except BatchRejectedError:
            raise UploadFileError(PDF_UNREADABLE) from None
    else:
        file_type = UploadFileType.TEXT
        try:
            text = data.decode("utf-8").removeprefix(UTF8_BOM)
        except UnicodeDecodeError:
            raise UploadFileError(NOT_UTF8) from None
    if "\x00" in text:
        raise UploadFileError(NUL_CHARACTER)
    if not text.strip():
        raise UploadFileError(NO_TEXT)
    return file_type, text
