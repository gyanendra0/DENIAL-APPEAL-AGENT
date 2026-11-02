"""`GET /health` answers without a login and says whether the database answers."""

import logging
import socket
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from src.api.app import create_app
from src.db import session as db_session
from src.db.session import create_session_factory
from tests.api.conftest import BASE_URL, RecordingSender, api_settings

# Nothing listens on port 1, so every connection is refused at once.
CLOSED_DATABASE_URL = "postgresql+psycopg://nobody:secret-word@127.0.0.1:1/nothing"
# The shortest connect timeout psycopg accepts.
SHORTEST_CONNECT_TIMEOUT_SECONDS = 2


def _client_on(engine: Engine) -> TestClient:
    """A client of an app on `engine`. Not entered as a context manager: the startup step needs
    a free connection too."""
    app = create_app(
        api_settings=api_settings(),
        session_factory=create_session_factory(engine),
        job_runner=lambda _account_id, _job_id: None,
        mail_sender=RecordingSender(),
    )
    return TestClient(app, base_url=BASE_URL)


@pytest.fixture
def silent_port() -> Iterator[int]:
    """A port that accepts a connection and never answers, like a frozen database."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        yield server.getsockname()[1]


def test_health_is_200_without_a_login_when_the_database_answers(
    api_client: TestClient,
) -> None:
    response = api_client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_health_is_503_without_the_error_text_when_the_database_is_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_engine(CLOSED_DATABASE_URL)
    client = _client_on(engine)
    with caplog.at_level(logging.WARNING, logger="src.api.routes.health"):
        response = client.get("/health")
    assert (response.status_code, response.json()) == (503, {"status": "unavailable"})
    assert caplog.messages == ["health: database unavailable (OperationalError)"]
    assert "secret-word" not in response.text
    engine.dispose()


def test_health_answers_while_every_pooled_connection_is_taken(
    test_database_url: str,
) -> None:
    engine = create_engine(test_database_url, pool_size=1, max_overflow=0, pool_timeout=1)
    client = _client_on(engine)
    with engine.connect():
        response = client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
    engine.dispose()


def test_health_is_503_when_the_database_accepts_but_never_answers(
    monkeypatch: pytest.MonkeyPatch, silent_port: int
) -> None:
    monkeypatch.setattr(db_session, "DB_CONNECT_TIMEOUT_SECONDS", SHORTEST_CONNECT_TIMEOUT_SECONDS)
    engine = create_engine(f"postgresql+psycopg://nobody@127.0.0.1:{silent_port}/nothing")
    response = _client_on(engine).get("/health")
    assert (response.status_code, response.json()) == (503, {"status": "unavailable"})
    engine.dispose()
