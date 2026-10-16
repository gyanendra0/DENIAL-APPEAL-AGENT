"""Refuse an oversized upload before its body is read.

FastAPI reads a whole `multipart/form-data` body (into a temporary file, with no total cap)
before any dependency or route code runs, the login check included. This middleware checks the
size the client declares in `Content-Length` first, so a huge body is answered `413` without
being received. The server never reads past the declared length, so the declared size is a cap.
The route still checks the exact file size against `UPLOAD_MAX_BYTES`.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config.settings import ApiSettings

UPLOAD_PATH = "/uploads"
# Room for the form around the file: boundaries, part headers and a file name of up to
# 255 characters (up to 4 bytes each in UTF-8).
MULTIPART_OVERHEAD_BYTES = 16_384
FILE_TOO_LARGE = "the file is larger than the upload limit"
LENGTH_REQUIRED = "the upload needs a Content-Length header"
BAD_LENGTH = "the Content-Length header is not a whole number"


class UploadSizeLimit:
    """ASGI middleware that checks the declared size of `POST /uploads` and nothing else."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != UPLOAD_PATH
        ):
            await self.app(scope, receive, send)
            return
        settings: ApiSettings = scope["app"].state.api_settings
        refusal = _refusal(_content_length(scope), settings.upload_max_bytes)
        if refusal is None:
            await self.app(scope, receive, send)
            return
        status_code, detail = refusal
        await JSONResponse({"detail": detail}, status_code=status_code)(scope, receive, send)


def _content_length(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            text: str = value.decode("latin-1")
            return text
    return None


def _refusal(content_length: str | None, upload_max_bytes: int) -> tuple[int, str] | None:
    """Return the status and message to answer with, or None to let the request through."""
    if content_length is None:
        return 411, LENGTH_REQUIRED
    if not content_length.isascii() or not content_length.isdigit():
        return 400, BAD_LENGTH
    if int(content_length) > upload_max_bytes + MULTIPART_OVERHEAD_BYTES:
        return 413, FILE_TOO_LARGE
    return None
