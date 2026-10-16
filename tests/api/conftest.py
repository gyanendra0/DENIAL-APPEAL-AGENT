"""The real app on `app_test`, inside one outer transaction that is rolled back after each test.

The app's own `get_db_session` runs unchanged: its commit only releases a savepoint, so a route
test sees its writes and leaves nothing behind. The app's job runner only records the jobs it
is given (`job_calls`), so no job runs and no model is called.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from src.api.app import create_app
from src.config.settings import ApiSettings

API_SECRET = "a-made-up-api-test-secret-of-forty-chars"  # noqa: S105  (made up, not a real secret)
API_TTL_MINUTES = 60
# The cookie is `Secure`, so the test client must talk HTTPS for it to be sent back.
BASE_URL = "https://testserver"


def api_settings() -> ApiSettings:
    """Made-up settings for an app built by a test."""
    return ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(API_SECRET),
        auth_token_ttl_minutes=API_TTL_MINUTES,
        _env_file=None,
    )


@pytest.fixture
def api_connection(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def db_session(api_connection: Connection) -> Iterator[Session]:
    """A session on the app's connection, to set up and check rows."""
    session = Session(bind=api_connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()


@pytest.fixture
def job_calls() -> list[tuple[int, int]]:
    """The (account id, job id) of every job the app handed to its runner, in order."""
    return []


@pytest.fixture
def api_client(
    api_connection: Connection, job_calls: list[tuple[int, int]]
) -> Iterator[TestClient]:
    factory = sessionmaker(
        bind=api_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )

    def record_job(account_id: int, job_id: int) -> None:
        job_calls.append((account_id, job_id))

    app = create_app(api_settings=api_settings(), session_factory=factory, job_runner=record_job)
    with TestClient(app, base_url=BASE_URL) as client:
        yield client
