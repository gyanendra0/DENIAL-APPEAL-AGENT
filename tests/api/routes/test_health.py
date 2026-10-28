"""`GET /health` answers without a login and says whether the database answers."""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from src.api.app import create_app
from src.db.session import create_session_factory
from tests.api.conftest import BASE_URL, RecordingSender, api_settings

# Nothing listens on port 1, so every connection is refused at once.
CLOSED_DATABASE_URL = "postgresql+psycopg://nobody:secret-word@127.0.0.1:1/nothing"


def test_health_is_200_without_a_login_when_the_database_answers(
    api_client: TestClient,
) -> None:
    response = api_client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_health_is_503_without_the_error_text_when_the_database_is_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_engine(CLOSED_DATABASE_URL, connect_args={"connect_timeout": 2})
    app = create_app(
        api_settings=api_settings(),
        session_factory=create_session_factory(engine),
        job_runner=lambda _account_id, _job_id: None,
        mail_sender=RecordingSender(),
    )
    # Not entered as a context manager: the startup step needs the database too.
    client = TestClient(app, base_url=BASE_URL)
    with caplog.at_level(logging.WARNING, logger="src.api.routes.health"):
        response = client.get("/health")
    assert (response.status_code, response.json()) == (503, {"status": "unavailable"})
    assert caplog.messages == ["health: database unavailable (OperationalError)"]
    assert "secret-word" not in response.text
    engine.dispose()
