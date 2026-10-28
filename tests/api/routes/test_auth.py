"""Sign-up, login and logout through the real app on `app_test`."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from src.api import login as login_module
from src.api.app import create_app
from src.api.dependencies import AUTH_COOKIE_NAME, get_now
from src.api.login_limit import TOO_MANY_LOGINS
from src.api.routes import auth as auth_module
from src.api.routes.auth import EMAIL_TAKEN, LOGIN_FAILED, SIGNUP_NOT_OPEN
from src.api.schemas import MAX_LOGIN_PASSWORD_CHARACTERS
from src.db.models import (
    Account,
    AuditAction,
    AuditEvent,
    EmailCode,
    LoginFailure,
    LoginKeyKind,
    User,
    UserRole,
)
from tests.api.conftest import (
    API_TTL_MINUTES,
    BASE_URL,
    RecordingSender,
    api_settings,
    code_in,
    signup_and_verify,
)

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
WRONG = "a-made-up-wrong-password"  # noqa: S105  (made up)
# In `ADMIN_EMAILS` of the test settings, so always allowed to sign up.
ADMIN = "admin@example.test"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _signup(
    client: TestClient, name: str = "Clinic One", email: str = "Ann@X.org"
) -> dict[str, str]:
    signed_up = signup_and_verify(client, name=name, email=email, password=PASSWORD)
    body: dict[str, str] = signed_up
    return body


def _counts(session: Session) -> tuple[int | None, int | None]:
    return (
        session.scalar(select(func.count()).select_from(Account)),
        session.scalar(select(func.count()).select_from(User)),
    )


def _set_cookie_header(client: TestClient, path: str, json: dict[str, str] | None) -> str:
    response = client.post(path, json=json)
    return response.headers["set-cookie"].lower()


def test_an_admin_email_acts_as_admin_and_logs_in_once_confirmed(
    api_client: TestClient, db_session: Session
) -> None:
    body = _signup(api_client)
    assert body["email"] == "ann@x.org"
    assert body["role"] == UserRole.ADMIN
    assert body["account_name"] == "Clinic One"
    assert AUTH_COOKIE_NAME in api_client.cookies
    assert api_client.get("/me").json()["user_id"] == body["user_id"]
    assert api_client.get("/me").json()["role"] == UserRole.ADMIN
    # Stored as reviewer like everyone: `ADMIN_EMAILS` makes the admin on each request.
    stored = db_session.scalars(select(User)).one()
    assert (stored.email, stored.role) == ("ann@x.org", UserRole.REVIEWER)


def _post_signup(client: TestClient, email: str) -> int:
    response = client.post("/auth/signup", json={"email": email, "password": PASSWORD})
    return response.status_code


def _verify(client: TestClient, sender: RecordingSender) -> dict[str, object]:
    verified = client.post("/auth/verify-email", json={"code": code_in(sender.sent[-1])})
    assert verified.status_code == 200, verified.text
    body: dict[str, object] = verified.json()
    return body


def _actions(session: Session) -> list[tuple[str, AuditAction]]:
    rows = session.execute(
        select(User.email, AuditEvent.action)
        .join(User, (User.account_id == AuditEvent.account_id) & (User.id == AuditEvent.user_id))
        .order_by(AuditEvent.id)
    )
    return [(email, action) for email, action in rows]


def test_an_allowed_email_joins_the_shared_account_as_a_reviewer(
    api_client: TestClient, db_session: Session, sent_mail: RecordingSender
) -> None:
    assert _post_signup(api_client, ADMIN) == 201
    admin = _verify(api_client, sent_mail)
    assert _post_signup(api_client, "Bob@X.org") == 201
    bob = _verify(api_client, sent_mail)
    assert (admin["role"], bob["role"]) == (UserRole.ADMIN, UserRole.REVIEWER)
    assert admin["account_id"] == bob["account_id"]
    assert bob["account_name"] == "Example Clinic"
    assert _counts(db_session) == (1, 2)


@pytest.mark.parametrize("email", ["ann@y.org", "ann@sub.x.org", "admin@example.test.evil.org"])
def test_signup_for_an_email_not_allowed_is_403_and_writes_and_sends_nothing(
    api_client: TestClient, db_session: Session, sent_mail: RecordingSender, email: str
) -> None:
    response = api_client.post("/auth/signup", json={"email": email, "password": PASSWORD})
    assert (response.status_code, response.json()) == (403, {"detail": SIGNUP_NOT_OPEN})
    assert "email_verify" not in response.headers.get("set-cookie", "")
    assert _counts(db_session) == (0, 0)
    assert db_session.scalars(select(EmailCode)).all() == []
    assert sent_mail.sent == []


def test_signup_with_an_account_name_is_422_and_creates_nothing(
    api_client: TestClient, db_session: Session
) -> None:
    response = api_client.post(
        "/auth/signup", json={"account_name": "Mine", "email": ADMIN, "password": PASSWORD}
    )
    assert response.status_code == 422
    assert _counts(db_session) == (0, 0)


def test_signup_with_a_taken_email_is_409_and_creates_nothing(
    api_client: TestClient, db_session: Session
) -> None:
    _signup(api_client, email=ADMIN)
    before = _counts(db_session)
    response = api_client.post("/auth/signup", json={"email": ADMIN, "password": PASSWORD})
    assert (response.status_code, response.json()["detail"]) == (409, EMAIL_TAKEN)
    assert _counts(db_session) == before


def test_signup_audits_nothing_until_the_code_is_typed(
    api_client: TestClient, db_session: Session, sent_mail: RecordingSender
) -> None:
    assert _post_signup(api_client, "ann@x.org") == 201
    assert _post_signup(api_client, "bob@x.org") == 201
    assert _actions(db_session) == []
    # The client's code cookie is Bob's, so Bob confirms first and creates the account; Ann
    # signs up again (replacing her unverified user) and joins it.
    _verify(api_client, sent_mail)
    assert _post_signup(api_client, "ann@x.org") == 201
    _verify(api_client, sent_mail)
    assert _actions(db_session) == [
        ("bob@x.org", AuditAction.ACCOUNT_CREATED),
        ("bob@x.org", AuditAction.EMAIL_VERIFIED),
        ("ann@x.org", AuditAction.USER_JOINED),
        ("ann@x.org", AuditAction.EMAIL_VERIFIED),
    ]


@pytest.mark.parametrize("length", [11, 129])
def test_signup_with_a_password_outside_12_to_128_is_422(
    api_client: TestClient, db_session: Session, length: int
) -> None:
    response = api_client.post("/auth/signup", json={"email": ADMIN, "password": "p" * length})
    assert response.status_code == 422
    assert _counts(db_session) == (0, 0)


def test_login_sets_the_cookie_with_every_attribute(api_client: TestClient) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    header = _set_cookie_header(
        api_client, "/auth/login", {"email": "ANN@x.org ", "password": PASSWORD}
    )
    assert header.startswith(f"{AUTH_COOKIE_NAME}=")
    for part in (
        "httponly",
        "samesite=strict",
        "secure",
        "path=/",
        f"max-age={API_TTL_MINUTES * 60}",
    ):
        assert part in header
    assert api_client.get("/me").status_code == 200


@pytest.mark.parametrize(
    ("email", "password"), [("nobody@x.org", PASSWORD), ("ann@x.org", "wrong-password")]
)
def test_unknown_email_and_wrong_password_give_the_same_401(
    api_client: TestClient, email: str, password: str
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    response = api_client.post("/auth/login", json={"email": email, "password": password})
    assert (response.status_code, response.json()) == (401, {"detail": LOGIN_FAILED})
    assert "set-cookie" not in response.headers


def test_login_with_an_unknown_email_checks_the_dummy_hash(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[str] = []
    monkeypatch.setattr(login_module, "verify_dummy_password", checked.append)
    api_client.post("/auth/login", json={"email": "nobody@x.org", "password": PASSWORD})
    assert checked == [PASSWORD]


def test_logout_clears_the_cookie(api_client: TestClient) -> None:
    _signup(api_client)
    response = api_client.post("/auth/logout")
    assert response.status_code == 204
    header = response.headers["set-cookie"].lower()
    assert header.startswith(f'{AUTH_COOKIE_NAME}=""') and "max-age=0" in header
    assert AUTH_COOKIE_NAME not in api_client.cookies
    assert api_client.get("/me").status_code == 401


def test_no_response_carries_the_password_or_the_hash(
    api_client: TestClient, db_session: Session
) -> None:
    responses = [
        api_client.post(
            "/auth/signup",
            json={"email": ADMIN, "password": PASSWORD},
        ),
        api_client.post("/auth/login", json={"email": ADMIN, "password": PASSWORD}),
        api_client.get("/me"),
    ]
    password_hash = db_session.scalars(select(User.password_hash)).one()
    assert password_hash is not None
    for response in responses:
        assert PASSWORD not in response.text
        assert password_hash not in response.text
        assert "password" not in response.json()


def test_signup_raises_when_the_new_user_cannot_be_read_back(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_module, "find_user_response", lambda *_, **__: None)
    with pytest.raises(RuntimeError, match="vanished"):
        _signup(api_client)


@pytest.mark.parametrize(
    "body",
    [
        {"email": "a@x.org", "password": "short-pw"},
        {"email": "a@x.org", "password": "short-pw" * 17},
        ["short-pw"],
        {"email": "a@x.org", "password": "short-pw", "extra": "short-pw"},
    ],
)
def test_a_422_names_the_field_but_never_repeats_what_was_sent(
    api_client: TestClient, body: object
) -> None:
    response = api_client.post("/auth/signup", json=body)
    assert response.status_code == 422
    assert "short-pw" not in response.text
    for error in response.json()["detail"]:
        assert set(error) == {"loc", "type", "msg"}


def test_login_with_a_password_over_the_cap_is_the_same_401_without_hashing(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    checked: list[str] = []
    monkeypatch.setattr(login_module, "verify_password", lambda *args: checked.append("x"))
    monkeypatch.setattr(login_module, "verify_dummy_password", checked.append)
    too_long = "p" * (MAX_LOGIN_PASSWORD_CHARACTERS + 1)
    response = api_client.post("/auth/login", json={"email": "ann@x.org", "password": too_long})
    assert (response.status_code, response.json()) == (401, {"detail": LOGIN_FAILED})
    assert checked == []


def test_login_at_the_cap_is_still_checked(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[str] = []
    monkeypatch.setattr(login_module, "verify_dummy_password", checked.append)
    at_cap = "p" * MAX_LOGIN_PASSWORD_CHARACTERS
    api_client.post("/auth/login", json={"email": "nobody@x.org", "password": at_cap})
    assert checked == [at_cap]


class _CommitFailsSession(Session):
    def commit(self) -> None:
        raise OperationalError("COMMIT", None, Exception("made-up commit failure"))


def test_signup_whose_commit_fails_is_not_answered_201(api_connection: Connection) -> None:
    factory: sessionmaker[Session] = sessionmaker(
        bind=api_connection,
        class_=_CommitFailsSession,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    app = create_app(
        api_settings=api_settings(),
        session_factory=factory,
        job_runner=lambda account_id, job_id: None,
        mail_sender=RecordingSender(),
    )
    # Not entered with `with`, so the startup sweep (whose commit would fail too) does not run.
    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    response = client.post(
        "/auth/signup",
        json={"email": ADMIN, "password": PASSWORD},
    )
    assert response.status_code == 500
    assert "set-cookie" not in response.headers


class _Clock:
    """The time the app sees; a test moves it on instead of waiting."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(api_client: TestClient) -> Iterator[_Clock]:
    fake = _Clock()
    api_client.app.dependency_overrides[get_now] = fake  # type: ignore[attr-defined]
    yield fake
    api_client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def _login(client: TestClient, email: str = "ann@x.org", password: str = WRONG) -> int:
    response = client.post("/auth/login", json={"email": email, "password": password})
    return response.status_code


def _failure_rows(session: Session) -> list[LoginKeyKind]:
    return list(session.scalars(select(LoginFailure.key_kind)).all())


@pytest.mark.parametrize("email", ["ann@x.org", "nobody@x.org"])
def test_after_five_failures_even_the_right_password_is_429(
    api_client: TestClient, clock: _Clock, email: str
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    for _ in range(5):
        assert _login(api_client, email) == 401
    clock.now = NOW + timedelta(minutes=1)
    response = api_client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert (response.status_code, response.json()) == (429, {"detail": TOO_MANY_LOGINS})
    assert response.headers["retry-after"] == str(14 * 60)
    assert "set-cookie" not in response.headers


def test_twenty_failures_from_one_ip_block_a_fresh_email(
    api_client: TestClient, clock: _Clock
) -> None:
    for number in range(20):
        assert _login(api_client, f"user{number}@x.org") == 401
    assert _login(api_client, "fresh@x.org") == 429


def test_after_the_window_the_login_works_again(api_client: TestClient, clock: _Clock) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    for _ in range(5):
        _login(api_client)
    clock.now = NOW + timedelta(minutes=15)
    assert _login(api_client, password=PASSWORD) == 200
    assert AUTH_COOKIE_NAME in api_client.cookies


def test_a_good_login_clears_the_email_count_but_not_the_ip_count(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    for _ in range(4):
        _login(api_client)
    assert _login(api_client, password=PASSWORD) == 200
    # The sign-up's own IP try stays too.
    assert _failure_rows(db_session) == [LoginKeyKind.IP] * 5
    for _ in range(4):
        assert _login(api_client) == 401
    assert _login(api_client, password=PASSWORD) == 200


def test_a_failure_row_survives_the_401(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    assert _login(api_client, "nobody@x.org") == 401
    assert sorted(_failure_rows(db_session)) == [LoginKeyKind.EMAIL, LoginKeyKind.IP]


def test_a_refused_try_writes_no_row(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    for _ in range(5):
        _login(api_client)
    assert _login(api_client) == 429
    assert len(_failure_rows(db_session)) == 10


def test_a_422_writes_no_row(api_client: TestClient, db_session: Session, clock: _Clock) -> None:
    assert _login(api_client, "not-an-email") == 422
    assert _failure_rows(db_session) == []


def test_a_password_over_the_cap_counts_as_a_failure(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    too_long = "p" * (MAX_LOGIN_PASSWORD_CHARACTERS + 1)
    assert _login(api_client, password=too_long) == 401
    assert len(_failure_rows(db_session)) == 2


def test_a_try_is_counted_before_its_password_is_checked(
    api_client: TestClient, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The server runs tries in parallel threads; a try that is counted only after its password
    # check is invisible to the tries that run beside it, and they all pass the limit.
    seen: list[int] = []
    real_authenticate = login_module.authenticate

    def authenticate(session: Session, *, email: str, password: str) -> User | None:
        seen.append(len(_failure_rows(session)))
        return real_authenticate(session, email=email, password=password)

    monkeypatch.setattr(auth_module, "authenticate", authenticate)
    assert _login(api_client) == 401
    assert seen == [2]


def test_a_429_and_its_log_lines_never_hold_the_email_password_or_ip(
    api_client: TestClient, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    caplog.set_level(logging.INFO)
    responses = [
        api_client.post("/auth/login", json={"email": "ann@x.org", "password": WRONG})
        for _ in range(5)
    ]
    responses.append(
        api_client.post("/auth/login", json={"email": "ann@x.org", "password": PASSWORD})
    )
    assert [response.status_code for response in responses] == [401] * 5 + [429]
    shown = caplog.text + "".join(
        response.text + str(dict(response.headers)) for response in responses
    )
    assert "login refused: too many failures" in caplog.text and "login failed" in caplog.text
    for secret in ("ann@x.org", WRONG, PASSWORD, "testclient"):
        assert secret not in shown
