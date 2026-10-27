""" "Continue with Google": `/auth/options`, the start, Google's return and the "name your account"
step, against a made-up Google (no network)."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.api.app import create_app
from src.api.dependencies import AUTH_COOKIE_NAME, get_now
from src.api.google_cookies import FLOW_COOKIE_NAME, SIGNUP_COOKIE_NAME
from src.api.routes.auth import ACCOUNT_NAME_TAKEN, EMAIL_TAKEN
from src.api.routes.google import (
    CALLBACK_PATH,
    GOOGLE_LINKED_ELSEWHERE,
    HOME_SCREEN,
    NAME_ACCOUNT_SCREEN,
    SIGNUP_EXPIRED,
)
from src.db.models import Account, AuditAction, AuditEvent, LoginFailure, User, UserIdentity
from tests.api.conftest import (
    BASE_URL,
    PUBLIC_URL,
    RecordingSender,
    code_in,
    signup_and_verify,
)
from tests.api.fake_google import (
    CLIENT_ID,
    CLIENT_SECRET,
    FakeGoogle,
    google_settings,
    id_claims,
    sign,
)

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
NEW_PASSWORD = "a-new-made-up-password"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
SUB = "100000000000000000001"


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def fake_google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def client(
    api_connection: Connection, fake_google: FakeGoogle, clock: _Clock, sender: RecordingSender
) -> Iterator[TestClient]:
    factory = sessionmaker(
        bind=api_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    app = create_app(
        api_settings=google_settings(),
        session_factory=factory,
        job_runner=lambda _account_id, _job_id: None,
        mail_sender=sender,
        google_http=fake_google.client(),
    )
    app.dependency_overrides[get_now] = clock
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as test_client:
        yield test_client


def _start(client: TestClient) -> dict[str, str]:
    """Start a sign-in and return the query of the Google address it sends the browser to."""
    response = client.get("/auth/google/start")
    assert response.status_code == 303, response.text
    return {
        key: values[0]
        for key, values in parse_qs(urlsplit(response.headers["location"]).query).items()
    }


def _sign_in(client: TestClient, fake: FakeGoogle, **claims: Any) -> tuple[int, str]:
    """Go through Google with an ID token for `claims`; return the callback's status and target."""
    query = _start(client)
    fake.id_token = sign({**id_claims(nonce=query["nonce"], now=NOW), **claims})
    response = client.get(CALLBACK_PATH, params={"code": "made-up-code", "state": query["state"]})
    return response.status_code, response.headers.get("location", "")


def _signup_with_password(
    client: TestClient, email: str = "ann@gmail.com", name: str = "One"
) -> int:
    signed_up = signup_and_verify(client, name=name, email=email, password=PASSWORD)
    client.cookies.clear()
    user_id: int = signed_up["user_id"]
    return user_id


def _me(client: TestClient) -> int:
    return client.get("/me").status_code


def _count(session: Session, model: type[Any]) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(model)) or 0


def _actions(session: Session) -> list[AuditAction]:
    session.expire_all()
    return list(session.scalars(select(AuditEvent.action).order_by(AuditEvent.id)))


# --- turned on or off ---


def test_options_say_google_is_off_and_every_google_route_is_404_without_its_keys(
    api_client: TestClient,
) -> None:
    assert api_client.get("/auth/options").json() == {"google": False}
    assert api_client.get("/auth/google/start", follow_redirects=False).status_code == 404
    assert api_client.get(CALLBACK_PATH, follow_redirects=False).status_code == 404
    assert api_client.get("/auth/google/signup").status_code == 404
    assert api_client.post("/auth/google/signup", json={"account_name": "X"}).status_code == 404


def test_options_say_google_is_on_with_its_keys(client: TestClient) -> None:
    assert client.get("/auth/options").json() == {"google": True}


# --- start ---


def test_start_sends_the_browser_to_google_with_the_public_callback(client: TestClient) -> None:
    response = client.get("/auth/google/start")
    location = urlsplit(response.headers["location"])
    query = {key: values[0] for key, values in parse_qs(location.query).items()}
    assert (response.status_code, location.netloc) == (303, "accounts.google.com")
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == f"{PUBLIC_URL}{CALLBACK_PATH}"
    assert query["code_challenge_method"] == "S256"
    assert "testserver" not in response.headers["location"]


def test_start_sets_a_lax_http_only_flow_cookie_for_the_callback_only(client: TestClient) -> None:
    cookie = client.get("/auth/google/start").headers["set-cookie"]
    assert cookie.startswith(f"{FLOW_COOKIE_NAME}=")
    for part in ("HttpOnly", "Secure", "SameSite=lax", f"Path={CALLBACK_PATH}", "Max-Age=600"):
        assert part in cookie
    assert CLIENT_SECRET not in cookie


def test_each_start_has_its_own_state_and_nonce(client: TestClient) -> None:
    first, second = _start(client), _start(client)
    assert first["state"] != second["state"] and first["nonce"] != second["nonce"]


# --- the callback: refusals ---


def test_a_callback_without_the_flow_cookie_is_refused_as_expired(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    response = client.get(CALLBACK_PATH, params={"code": "made-up-code", "state": "x"})
    assert (response.status_code, response.headers["location"]) == (
        303,
        "/app/login?google=expired",
    )
    assert fake_google.swaps == []


def test_a_flow_cookie_older_than_ten_minutes_is_refused_as_expired(
    client: TestClient, fake_google: FakeGoogle, clock: _Clock
) -> None:
    query = _start(client)
    clock.now = NOW + timedelta(minutes=10)
    response = client.get(CALLBACK_PATH, params={"code": "made-up-code", "state": query["state"]})
    assert response.headers["location"] == "/app/login?google=expired"
    assert fake_google.swaps == []


@pytest.mark.parametrize(
    "params",
    [
        {"code": "made-up-code", "state": "someone-elses-state"},
        {"code": "made-up-code"},
        {"code": "made-up-code", "state": "x" * 3000},
    ],
)
def test_a_wrong_or_missing_state_is_refused_without_calling_google(
    client: TestClient, fake_google: FakeGoogle, params: dict[str, str]
) -> None:
    _start(client)
    response = client.get(CALLBACK_PATH, params=params)
    assert response.headers["location"] == "/app/login?google=failed"
    assert fake_google.swaps == []


@pytest.mark.parametrize("params", [{"error": "access_denied"}, {}, {"code": "x" * 3000}])
def test_a_cancel_or_a_missing_code_is_refused_without_calling_google(
    client: TestClient, fake_google: FakeGoogle, params: dict[str, str]
) -> None:
    query = _start(client)
    response = client.get(CALLBACK_PATH, params={"state": query["state"], **params})
    assert response.headers["location"] == "/app/login?google=failed"
    assert fake_google.swaps == []


def test_a_bad_id_token_is_refused_and_writes_nothing(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    _signup_with_password(client)
    assert _sign_in(client, fake_google, aud="someone-elses-client") == (
        303,
        "/app/login?google=failed",
    )
    assert _count(db_session, UserIdentity) == 0
    assert AUTH_COOKIE_NAME not in client.cookies


def test_an_id_token_from_another_start_is_refused_and_writes_nothing(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    _signup_with_password(client)
    old = _start(client)
    query = _start(client)
    fake_google.id_token = sign(id_claims(nonce=old["nonce"], now=NOW))
    response = client.get(CALLBACK_PATH, params={"code": "made-up-code", "state": query["state"]})
    assert response.headers["location"] == "/app/login?google=failed"
    assert (_count(db_session, UserIdentity), _actions(db_session)) == (
        0,
        [AuditAction.ACCOUNT_CREATED, AuditAction.EMAIL_VERIFIED],
    )
    assert AUTH_COOKIE_NAME not in client.cookies


def test_every_callback_answer_ends_the_flow_cookie(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    query = _start(client)
    response = client.get(CALLBACK_PATH, params={"error": "access_denied", "state": query["state"]})
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f'{FLOW_COOKIE_NAME}=""') and "Max-Age=0" in cookie
    second = client.get(CALLBACK_PATH, params={"code": "made-up-code", "state": query["state"]})
    assert second.headers["location"] == "/app/login?google=expired"


def test_an_unverified_email_is_refused_and_writes_nothing(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    _signup_with_password(client)
    assert (
        _sign_in(client, fake_google, email_verified=False)[1] == "/app/login?google=not_verified"
    )
    assert (_count(db_session, UserIdentity), _actions(db_session)) == (
        0,
        [AuditAction.ACCOUNT_CREATED, AuditAction.EMAIL_VERIFIED],
    )
    assert AUTH_COOKIE_NAME not in client.cookies


def test_a_user_on_a_domain_google_does_not_manage_must_use_the_password(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    _signup_with_password(client, "ann@clinic.example")
    location = _sign_in(client, fake_google, email="ann@clinic.example")[1]
    assert location == "/app/login?google=use_password"
    assert (_count(db_session, UserIdentity), _actions(db_session)) == (
        0,
        [AuditAction.ACCOUNT_CREATED, AuditAction.EMAIL_VERIFIED],
    )


def test_a_new_email_on_a_domain_google_does_not_manage_must_sign_up_with_a_password(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    status, location = _sign_in(client, fake_google, email="ann@clinic.example")
    assert (status, location) == (303, "/app/login?google=use_signup")
    assert SIGNUP_COOKIE_NAME not in client.cookies
    assert AUTH_COOKIE_NAME not in client.cookies
    assert (_count(db_session, User), _count(db_session, UserIdentity), _actions(db_session)) == (
        0,
        0,
        [],
    )


def test_a_new_workspace_email_is_sent_to_name_its_account(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    location = _sign_in(client, fake_google, email="ann@clinic.example", hd="clinic.example")[1]
    assert location == NAME_ACCOUNT_SCREEN


# --- the callback: logging in and linking ---


def test_an_existing_gmail_user_is_linked_audited_and_logged_in(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    user_id = _signup_with_password(client)

    assert _sign_in(client, fake_google) == (303, HOME_SCREEN)

    me = client.get("/me")
    assert (me.status_code, me.json()["user_id"]) == (200, user_id)
    assert _actions(db_session) == [
        AuditAction.ACCOUNT_CREATED,
        AuditAction.EMAIL_VERIFIED,
        AuditAction.GOOGLE_LINKED,
    ]
    [link] = db_session.scalars(select(UserIdentity)).all()
    assert (link.user_id, link.subject) == (user_id, SUB)


def test_a_linked_user_logs_in_again_without_a_new_audit_event(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    _signup_with_password(client)
    _sign_in(client, fake_google)
    client.cookies.clear()

    assert _sign_in(client, fake_google) == (303, HOME_SCREEN)
    assert _me(client) == 200
    assert _actions(db_session) == [
        AuditAction.ACCOUNT_CREATED,
        AuditAction.EMAIL_VERIFIED,
        AuditAction.GOOGLE_LINKED,
    ]


def test_the_login_cookie_is_strict_like_a_password_login(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    _signup_with_password(client)
    query = _start(client)
    fake_google.id_token = sign(id_claims(nonce=query["nonce"], now=NOW))
    response = client.get(CALLBACK_PATH, params={"code": "made-up-code", "state": query["state"]})
    [login_cookie] = [
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{AUTH_COOKIE_NAME}=")
    ]
    for part in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert part in login_cookie


def test_a_password_change_ends_a_google_session(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    _signup_with_password(client)
    _sign_in(client, fake_google)
    google_session = client.cookies.get(AUTH_COOKIE_NAME)
    client.cookies.clear()
    client.post("/auth/login", json={"email": "ann@gmail.com", "password": PASSWORD})
    changed = client.post(
        "/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert changed.status_code == 204, changed.text

    client.cookies.clear()
    assert google_session is not None
    client.cookies.set(AUTH_COOKIE_NAME, google_session)
    assert _me(client) == 401


def test_a_google_sign_in_does_not_clear_the_emails_password_failures(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    _signup_with_password(client)
    client.post("/auth/login", json={"email": "ann@gmail.com", "password": "a-wrong-password"})
    before = _count(db_session, LoginFailure)
    assert before > 0

    _sign_in(client, fake_google)

    assert _count(db_session, LoginFailure) == before


# --- a Google account with no user: name the account ---


def test_a_new_google_account_is_sent_to_name_its_account(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    query = _start(client)
    fake_google.id_token = sign(id_claims(nonce=query["nonce"], now=NOW))
    response = client.get(CALLBACK_PATH, params={"code": "made-up-code", "state": query["state"]})

    assert (response.status_code, response.headers["location"]) == (303, NAME_ACCOUNT_SCREEN)
    [signup_cookie] = [
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{SIGNUP_COOKIE_NAME}=")
    ]
    for part in ("HttpOnly", "Secure", "SameSite=strict", "Path=/auth/google/signup"):
        assert part in signup_cookie
    assert AUTH_COOKIE_NAME not in client.cookies
    assert (_count(db_session, User), _count(db_session, UserIdentity)) == (0, 0)
    assert client.get("/auth/google/signup").json() == {"email": "ann@gmail.com"}


def test_naming_the_account_creates_an_admin_with_no_password_and_logs_in(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    _sign_in(client, fake_google)

    response = client.post("/auth/google/signup", json={"account_name": "  Clinic One "})

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["email"], body["role"], body["account_name"]) == (
        "ann@gmail.com",
        "admin",
        "Clinic One",
    )
    assert _me(client) == 200
    user = db_session.get(User, body["user_id"])
    assert user is not None and user.password_hash is None
    assert _actions(db_session) == [AuditAction.ACCOUNT_CREATED]
    assert _count(db_session, UserIdentity) == 1
    assert client.get("/auth/google/signup").status_code == 401

    client.cookies.clear()
    assert _sign_in(client, fake_google) == (303, HOME_SCREEN)
    assert (
        client.post(
            "/auth/login", json={"email": "ann@gmail.com", "password": PASSWORD}
        ).status_code
        == 401
    )


def test_a_user_with_no_password_sets_one_with_forgot_password(
    client: TestClient, fake_google: FakeGoogle, sender: RecordingSender
) -> None:
    _sign_in(client, fake_google)
    assert client.post("/auth/google/signup", json={"account_name": "Clinic"}).status_code == 201
    client.cookies.clear()

    asked = client.post("/auth/password-reset", json={"email": "ann@gmail.com"})
    assert asked.status_code == 202
    body = {"email": "ann@gmail.com", "code": code_in(sender.sent[0]), "new_password": NEW_PASSWORD}
    confirmed = client.post("/auth/password-reset/confirm", json=body)
    assert confirmed.status_code == 204

    login = client.post("/auth/login", json={"email": "ann@gmail.com", "password": NEW_PASSWORD})
    assert login.status_code == 200
    change = client.post(
        "/me/password", json={"current_password": NEW_PASSWORD, "new_password": PASSWORD}
    )
    assert change.status_code == 204, change.text


def test_a_taken_name_can_be_changed_while_the_cookie_lasts(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    _signup_with_password(client, "bob@x.org", "Clinic")
    _sign_in(client, fake_google)

    taken = client.post("/auth/google/signup", json={"account_name": "Clinic"})
    assert (taken.status_code, taken.json()["detail"]) == (409, ACCOUNT_NAME_TAKEN)
    assert (
        client.post("/auth/google/signup", json={"account_name": "Clinic Two"}).status_code == 201
    )


def test_an_email_taken_meanwhile_is_409(client: TestClient, fake_google: FakeGoogle) -> None:
    _sign_in(client, fake_google)
    pending = client.cookies.get(SIGNUP_COOKIE_NAME)
    _signup_with_password(client, "ann@gmail.com", "First")
    assert pending is not None
    client.cookies.set(SIGNUP_COOKIE_NAME, pending, path="/auth/google/signup")

    taken = client.post("/auth/google/signup", json={"account_name": "Second"})
    assert (taken.status_code, taken.json()["detail"]) == (409, EMAIL_TAKEN)


def test_a_google_account_linked_to_another_user_creates_nothing(
    client: TestClient, fake_google: FakeGoogle, db_session: Session
) -> None:
    _sign_in(client, fake_google)
    client.post("/auth/google/signup", json={"account_name": "First"})
    client.cookies.clear()
    # The same Google account now has another email, which no user has.
    _sign_in(client, fake_google, email="ann.new@gmail.com")
    before = _count(db_session, Account)

    refused = client.post("/auth/google/signup", json={"account_name": "Second"})

    assert (refused.status_code, refused.json()["detail"]) == (409, GOOGLE_LINKED_ELSEWHERE)
    assert _count(db_session, Account) == before


def test_naming_without_a_pending_sign_up_or_after_ten_minutes_is_401(
    client: TestClient, fake_google: FakeGoogle, clock: _Clock
) -> None:
    missing = client.post("/auth/google/signup", json={"account_name": "Clinic"})
    assert (missing.status_code, missing.json()["detail"]) == (401, SIGNUP_EXPIRED)

    _sign_in(client, fake_google)
    clock.now = NOW + timedelta(minutes=10)
    assert client.get("/auth/google/signup").status_code == 401
    assert client.post("/auth/google/signup", json={"account_name": "Clinic"}).status_code == 401


def test_a_bad_account_name_or_an_extra_field_is_422(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    _sign_in(client, fake_google)
    for body in (
        {"account_name": "   "},
        {"account_name": "x" * 201},
        {"account_name": "Clinic", "email": "eve@x.org"},
    ):
        assert client.post("/auth/google/signup", json=body).status_code == 422


# --- what is logged ---


def test_nothing_secret_is_logged_or_put_in_an_address(
    client: TestClient, fake_google: FakeGoogle, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        query = _start(client)
        token = sign(id_claims(nonce=query["nonce"], now=NOW))
        fake_google.id_token = token
        response = client.get(
            CALLBACK_PATH, params={"code": "made-up-code", "state": query["state"]}
        )
        client.post("/auth/google/signup", json={"account_name": "Clinic"})
    # The test client logs every address it asks for (`httpx2`); in production uvicorn's access
    # log does the same, which the deployment turns off. The app's own logs and its calls to
    # Google (`httpx`) are checked here.
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name != "httpx2")
    for secret in (
        "made-up-code",
        query["state"],
        query["nonce"],
        token,
        CLIENT_SECRET,
        "ann@gmail.com",
        SUB,
    ):
        assert secret not in logged
        assert secret not in response.headers["location"]
