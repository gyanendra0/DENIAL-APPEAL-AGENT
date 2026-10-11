"""Read the text of a PDF file, page by page.

This is the only module that imports the PDF library. It returns plain text and knows nothing
about what the file holds. A file that cannot be read, is encrypted or has no text that can be
extracted (a scan, for example) is rejected outright.
"""

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from src.ingest.marketplace_denials import BatchRejectedError


def read_pdf_pages(path: Path) -> list[str]:
    """Return the text of every page of the PDF at `path`, in page order.

    Raises `BatchRejectedError` for a file that is not a readable PDF, is encrypted, or holds
    no text. A missing file raises `FileNotFoundError`.
    """
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise BatchRejectedError([f"{path.name}: the PDF is encrypted"])
        pages = [page.extract_text() for page in reader.pages]
    except PyPdfError as exc:
        raise BatchRejectedError([f"{path.name}: not a readable PDF file"]) from exc
    if not any(page.strip() for page in pages):
        raise BatchRejectedError([f"{path.name}: the PDF holds no text that can be extracted"])
    return pages
