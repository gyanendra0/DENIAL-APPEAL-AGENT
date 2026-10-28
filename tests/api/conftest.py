"""The real app on `app_test`, inside one outer transaction that is rolled back after each test.

The app's own `get_db_session` runs unchanged: its commit only releases a savepoint, so a route
test sees its writes and leaves nothing behind. The app's job runner only records the jobs it
is given (`job_calls`), so no job runs and no model is called; its mail sender only records the
messages it is given (`sent_mail`), so no email leaves.
"""

import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.api.app import create_app
from src.api.mail import MailMessage
from src.api.signup import create_member
from src.config.settings import ApiSettings
from src.db.models import Account, AccountKind, AuditAction, User, UserRole
from src.review.audit import record_audit_event

API_SECRET = "a-made-up-api-test-secret-of-forty-chars"  # noqa: S105  (made up, not a real secret)
API_TTL_MINUTES = 60
# The cookie is `Secure`, so the test client must talk HTTPS for it to be sent back.
BASE_URL = "https://testserver"
PUBLIC_URL = "https://app.example.test"
# When a user a test adds straight to the database confirmed their email, so login lets them in.
VERIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)
# The emailed code sits alone on an indented line.
_CODE_LINE = re.compile(r"^ {4}([0-9]{6})$", re.MULTILINE)


def api_settings() -> ApiSettings:
    """Made-up settings for an app built by a test. Besides the admin, every `@x.org` email may
    sign up, as most tests use one."""
    return ApiSettings(  # type: ignore[call-arg]
        shared_account_name="Example Clinic",
        admin_emails="admin@example.test",
        signup_allowed_domains="x.org",
        auth_token_secret=SecretStr(API_SECRET),
        auth_token_ttl_minutes=API_TTL_MINUTES,
        app_public_url=PUBLIC_URL,
        _env_file=None,
    )


class RecordingSender:
    """A mail sender that keeps every message instead of sending it."""

    def __init__(self) -> None:
        self.sent: list[MailMessage] = []

    def send(self, message: MailMessage) -> None:
        self.sent.append(message)


def code_in(message: MailMessage) -> str:
    """The 6-digit code an emailed message carries."""
    found = _CODE_LINE.search(message.body)
    assert found is not None, "no code in the email"
    return found.group(1)


def _with_admin(settings: ApiSettings, email: str) -> ApiSettings:
    return settings.model_copy(update={"admin_emails": f"{settings.admin_emails},{email}"})


def add_member(
    session: Session,
    *,
    account_name: str,
    email: str,
    password: str | None,
    verified_at: datetime | None,
) -> User:
    """Add a user with `email` straight to the account named `account_name` (made first if
    there is none), as sign-up would with `SHARED_ACCOUNT_NAME` set to that name. Lets a test
    put users in separate accounts. Stored as `reviewer`, like every sign-up."""
    settings = api_settings().model_copy(update={"shared_account_name": account_name})
    return create_member(
        session,
        settings=settings,
        kind=AccountKind.SHARED,
        email=email,
        password=password,
        verified_at=verified_at,
    )


def add_old_unverified_admin(session: Session, *, account_name: str, email: str) -> User:
    """Add an account made before the shared account: its one `admin`, who never confirmed
    `email`, and the `account_created` row that sign-up then wrote at once."""
    account = Account(name=account_name)
    session.add(account)
    session.flush()
    user = User(account_id=account.id, email=email, role=UserRole.ADMIN, email_verified_at=None)
    session.add(user)
    session.flush()
    record_audit_event(session, account.id, user.id, AuditAction.ACCOUNT_CREATED, None)
    session.flush()
    return user


def signup_and_verify(client: TestClient, *, name: str, email: str, password: str) -> Any:
    """Sign up, type the emailed code and return the logged-in user's JSON (as the code
    screen would). The client is then logged in as that user; the sign-up email is taken out
    of the recorded mail again, so a test sees only the mail it caused itself.

    The user signs up as an admin of the account named `name`: for the sign-up only, the app's
    shared account is that name, and from then on the email is in its `ADMIN_EMAILS`. Two
    names give two separate accounts."""
    state = client.app.state  # type: ignore[attr-defined]
    with_admin = _with_admin(state.api_settings, email)
    state.api_settings = with_admin.model_copy(update={"shared_account_name": name})
    try:
        response = client.post("/auth/signup", json={"email": email, "password": password})
    finally:
        state.api_settings = with_admin
    assert response.status_code == 201, response.text
    sender: RecordingSender = client.app.state.mail_sender  # type: ignore[attr-defined]
    verified = client.post("/auth/verify-email", json={"code": code_in(sender.sent[-1])})
    assert verified.status_code == 200, verified.text
    sender.sent.pop()
    return verified.json()


def waits_on_a_lock(engine: Engine, pid: int, timeout: float = 5.0) -> bool:
    """True once database backend `pid` waits on a lock; False after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.scalar(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            if waiting:
                return True
            time.sleep(0.02)
    return False


# Made up; the password every `signup_individual` user logs in with.
INDIVIDUAL_PASSWORD = "a-made-up-individual-password"  # noqa: S105  (made up)


def signup_individual(client: TestClient, sender: RecordingSender, email: str) -> Any:
    """Open sign-up to individuals, sign `email` up into a new personal account, type the
    emailed code and return the logged-in user's JSON. The client is then logged in as them."""
    state = client.app.state  # type: ignore[attr-defined]
    state.api_settings = state.api_settings.model_copy(update={"individual_signup_enabled": True})
    client.cookies.clear()
    signed_up = client.post("/auth/signup", json={"email": email, "password": INDIVIDUAL_PASSWORD})
    assert signed_up.status_code == 201, signed_up.text
    verified = client.post("/auth/verify-email", json={"code": code_in(sender.sent[-1])})
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert (body["account_kind"], body["role"]) == ("personal", "admin")
    return body


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
def sent_mail() -> RecordingSender:
    """The app's mail sender; `.sent` holds every message the app sent, in order."""
    return RecordingSender()


@pytest.fixture
def api_client(
    api_connection: Connection, job_calls: list[tuple[int, int]], sent_mail: RecordingSender
) -> Iterator[TestClient]:
    factory = sessionmaker(
        bind=api_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )

    def record_job(account_id: int, job_id: int) -> None:
        job_calls.append((account_id, job_id))

    app = create_app(
        api_settings=api_settings(),
        session_factory=factory,
        job_runner=record_job,
        mail_sender=sent_mail,
    )
    with TestClient(app, base_url=BASE_URL) as client:
        yield client
