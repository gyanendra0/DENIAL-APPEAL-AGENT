"""The declared-size check in front of `POST /uploads`. Every body here is made up."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.upload_limit import (
    BAD_LENGTH,
    FILE_TOO_LARGE,
    LENGTH_REQUIRED,
    MULTIPART_OVERHEAD_BYTES,
    UploadSizeLimit,
)
from src.config.settings import ApiSettings
from src.db.models import Job
from tests.api.conftest import API_SECRET, BASE_URL

UPLOAD_MAX_BYTES = 100
LARGEST_ALLOWED = UPLOAD_MAX_BYTES + MULTIPART_OVERHEAD_BYTES


@pytest.fixture
def client() -> Iterator[TestClient]:
    """An app with the middleware and routes that only say how many bytes they received."""
    app = FastAPI()
    app.state.api_settings = ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(API_SECRET), upload_max_bytes=UPLOAD_MAX_BYTES, _env_file=None
    )
    app.add_middleware(UploadSizeLimit)

    async def received(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    app.add_api_route("/uploads", received, methods=["POST"])
    app.add_api_route("/other", received, methods=["POST"])
    with TestClient(app, base_url=BASE_URL) as test_client:
        yield test_client


def _post(client: TestClient, path: str, length: str) -> tuple[int, dict[str, object]]:
    response = client.post(path, content=b"x", headers={"content-length": length})
    body: dict[str, object] = response.json()
    return response.status_code, body


def test_a_declared_size_over_the_limit_is_413_before_the_body_is_read(
    client: TestClient,
) -> None:
    assert _post(client, "/uploads", str(LARGEST_ALLOWED + 1)) == (
        413,
        {"detail": FILE_TOO_LARGE},
    )


def test_a_declared_size_at_the_limit_reaches_the_route(client: TestClient) -> None:
    body = b"x" * LARGEST_ALLOWED
    response = client.post("/uploads", content=body)
    assert (response.status_code, response.json()) == (200, {"received": LARGEST_ALLOWED})


def test_an_upload_without_a_declared_size_is_411(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b"made-up"

    response = client.post("/uploads", content=chunks())
    assert "content-length" not in response.request.headers
    assert (response.status_code, response.json()) == (411, {"detail": LENGTH_REQUIRED})


# `\xb2` is a superscript two in Latin-1: Python counts it as a digit, `int` refuses it.
@pytest.mark.parametrize("length", [b"ten", b"-1", b"1.5", b"\xb2"])
def test_a_declared_size_that_is_not_a_whole_number_is_400(
    client: TestClient, length: bytes
) -> None:
    response = client.post("/uploads", content=b"x", headers={b"content-length": length})
    assert (response.status_code, response.json()) == (400, {"detail": BAD_LENGTH})


def test_a_trailing_slash_is_checked_too(client: TestClient) -> None:
    assert _post(client, "/uploads/", str(LARGEST_ALLOWED + 1))[0] == 413


def test_other_paths_are_not_checked(client: TestClient) -> None:
    response = client.post("/other", content=b"x" * (LARGEST_ALLOWED + 1))
    assert (response.status_code, response.json()) == (200, {"received": LARGEST_ALLOWED + 1})


def test_other_methods_on_the_upload_path_are_not_checked(client: TestClient) -> None:
    assert client.get("/uploads").status_code == 405


def test_the_real_app_refuses_a_huge_upload_before_the_login_check(
    api_client: TestClient, db_session: Session
) -> None:
    code, body = _post(api_client, "/uploads", str(20 * 1024**3))
    assert (code, body) == (413, {"detail": FILE_TOO_LARGE})
    assert db_session.scalars(select(Job)).all() == []
