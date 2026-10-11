"""Read the text of a PDF file, page by page.

This is the only module that imports the PDF library. It returns plain text and knows nothing
about what the file holds. A file that cannot be read, is encrypted or has no text that can be
extracted (a scan, for example) is rejected outright.
"""

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from src.ingest.marketplace_denials import BatchRejectedError

# The library raises its own errors for most damaged files, and these plain ones for some:
# a damaged cross-reference table or a damaged object reaches code that expects a sound file.
DAMAGED_FILE_ERRORS = (
    PyPdfError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    RecursionError,
)


def read_pdf_pages(path: Path) -> list[str]:
    """Return the text of every page of the PDF at `path`, in page order.

    Raises `BatchRejectedError` for a file that is not a readable PDF, is encrypted, or holds
    no text, and for a path that cannot be opened (a folder, a file with no read permission).
    A missing file raises `FileNotFoundError`.
    """
    try:
        reader = PdfReader(path)
        encrypted = reader.is_encrypted
        pages = [] if encrypted else [page.extract_text() for page in reader.pages]
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise BatchRejectedError([f"{path.name}: the file cannot be opened"]) from exc
    except DAMAGED_FILE_ERRORS as exc:
        raise BatchRejectedError([f"{path.name}: not a readable PDF file"]) from exc
    if encrypted:
        raise BatchRejectedError([f"{path.name}: the PDF is encrypted"])
    if not any(page.strip() for page in pages):
        raise BatchRejectedError([f"{path.name}: the PDF holds no text that can be extracted"])
    return pages
