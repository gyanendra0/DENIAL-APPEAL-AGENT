"""Sign-up with an emailed code through the real app on `app_test`: sign-up sends a code and does
not log in; the code logs in; a new code waits out its limits; login refuses an unverified email."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api.dependencies import AUTH_COOKIE_NAME, get_now
from src.api.email_codes import CODE_INVALID
from src.api.email_verification import MAIL_UNAVAILABLE
from src.api.login_limit import TOO_MANY_LOGINS
from src.api.routes.auth import (
    EMAIL_NOT_VERIFIED,
    EMAIL_NOT_VERIFIED_CODE,
    EMAIL_TAKEN,
    LOGIN_FAILED,
)
from src.api.routes.verify_email import (
    ALREADY_VERIFIED,
    CODE_SENT,
    CODE_TOO_SOON,
    VERIFY_EXPIRED,
)
from src.api.verify_cookie import VERIFY_COOKIE_NAME, VERIFY_COOKIE_PATH
from src.db.models import (
    Account,
    AuditAction,
    AuditEvent,
    EmailCode,
    EmailCodePurpose,
    MailSend,
    User,
)
from tests.api.conftest import RecordingSender, code_in

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
WRONG = "a-made-up-wrong-password"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
VERIFY_PATH = "/auth/verify-email"
RESEND_PATH = "/auth/verify-email/resend"


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


def _signup(client: TestClient, email: str = "ann@x.org") -> int:
    response = client.post("/auth/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    user_id: int = response.json()["user_id"]
    return user_id


def _verify(client: TestClient, code: str) -> int:
    return client.post(VERIFY_PATH, json={"code": code}).status_code


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _user(session: Session, user_id: int) -> User | None:
    session.expire_all()
    return session.get(User, user_id)


def _count(
    session: Session, model: type[Account] | type[User] | type[EmailCode] | type[MailSend]
) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(model)) or 0


def _actions(session: Session) -> list[AuditAction]:
    session.expire_all()
    return list(session.scalars(select(AuditEvent.action).order_by(AuditEvent.id)))


# --- sign-up ---


def test_signup_emails_a_code_and_does_not_log_in(
    api_client: TestClient, db_session: Session, clock: _Clock, sent_mail: RecordingSender
) -> None:
    response = api_client.post("/auth/signup", json={"email": "Ann@X.org", "password": PASSWORD})
    assert response.status_code == 201
    assert response.json()["email"] == "ann@x.org"
    assert AUTH_COOKIE_NAME not in api_client.cookies
    assert api_client.get("/me").status_code == 401
    header = response.headers["set-cookie"].lower()
    assert header.startswith(f"{VERIFY_COOKIE_NAME}=")
    for part in ("httponly", "samesite=strict", f"path={VERIFY_COOKIE_PATH}", "secure"):
        assert part in header
    (message,) = sent_mail.sent
    assert (message.to, message.subject) == ("ann@x.org", "Your sign-up code")
    user = _user(db_session, response.json()["user_id"])
    assert user is not None and user.email_verified_at is None
    assert (_count(db_session, EmailCode), _count(db_session, MailSend)) == (1, 1)
    # Nothing is audited until the code is typed.
    assert _actions(db_session) == []


def test_the_right_code_verifies_logs_in_and_clears_the_code(
    api_client: TestClient, db_session: Session, clock: _Clock, sent_mail: RecordingSender
) -> None:
    user_id = _signup(api_client)
    response = api_client.post(VERIFY_PATH, json={"code": code_in(sent_mail.sent[0])})
    assert response.status_code == 200
    assert (response.json()["user_id"], response.json()["email"]) == (user_id, "ann@x.org")
    assert api_client.get("/me").json()["user_id"] == user_id
    assert VERIFY_COOKIE_NAME not in api_client.cookies
    user = _user(db_session, user_id)
    assert user is not None and user.email_verified_at == NOW
    assert _count(db_session, EmailCode) == 0
    assert _actions(db_session) == [AuditAction.ACCOUNT_CREATED, AuditAction.EMAIL_VERIFIED]


def test_a_wrong_code_is_400_counts_and_the_fifth_kills_the_code(
    api_client: TestClient, db_session: Session, clock: _Clock, sent_mail: RecordingSender
) -> None:
    user_id = _signup(api_client)
    code = code_in(sent_mail.sent[0])
    response = api_client.post(VERIFY_PATH, json={"code": _wrong(code)})
    assert (response.status_code, response.json()) == (400, {"detail": CODE_INVALID})
    for _ in range(4):
        assert _verify(api_client, _wrong(code)) == 400
    assert _verify(api_client, code) == 400
    assert AUTH_COOKIE_NAME not in api_client.cookies
    user = _user(db_session, user_id)
    assert user is not None and user.email_verified_at is None


def test_an_expired_code_is_400(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    clock.now = NOW + timedelta(minutes=10)
    assert _verify(api_client, code_in(sent_mail.sent[0])) == 400


@pytest.mark.parametrize("code", ["12345", "1234567", "12345a", " 123456"])
def test_a_code_that_is_not_six_digits_is_422(
    api_client: TestClient, clock: _Clock, code: str
) -> None:
    _signup(api_client)
    assert _verify(api_client, code) == 422


@pytest.mark.parametrize("path", [VERIFY_PATH, RESEND_PATH])
def test_without_a_usable_cookie_both_routes_are_401(
    api_client: TestClient, clock: _Clock, path: str
) -> None:
    body = {"code": "123456"} if path == VERIFY_PATH else None
    response = api_client.post(path, json=body)
    assert (response.status_code, response.json()) == (401, {"detail": VERIFY_EXPIRED})
    api_client.cookies.set(VERIFY_COOKIE_NAME, "made.up.value", path=VERIFY_COOKIE_PATH)
    assert api_client.post(path, json=body).status_code == 401
    api_client.cookies.clear()
    _signup(api_client)
    clock.now = NOW + timedelta(hours=24)
    assert api_client.post(path, json=body).status_code == 401


def test_a_new_sign_up_replaces_an_unverified_holder_of_the_email(
    api_client: TestClient, db_session: Session, clock: _Clock, sent_mail: RecordingSender
) -> None:
    first = _signup(api_client)
    first_code = code_in(sent_mail.sent[0])
    first_cookie = api_client.cookies[VERIFY_COOKIE_NAME]
    api_client.cookies.clear()
    second = _signup(api_client)
    assert _user(db_session, first) is None and _user(db_session, second) is not None
    assert _count(db_session, Account) == 1
    # The replaced user's screen no longer works.
    api_client.cookies.clear()
    api_client.cookies.set(VERIFY_COOKIE_NAME, first_cookie, path=VERIFY_COOKIE_PATH)
    response = api_client.post(VERIFY_PATH, json={"code": first_code})
    assert (response.status_code, response.json()) == (401, {"detail": VERIFY_EXPIRED})


def test_a_verified_holder_of_the_email_keeps_it(
    api_client: TestClient, db_session: Session, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    assert _verify(api_client, code_in(sent_mail.sent[0])) == 200
    api_client.cookies.clear()
    response = api_client.post("/auth/signup", json={"email": "ann@x.org", "password": PASSWORD})
    assert (response.status_code, response.json()) == (409, {"detail": EMAIL_TAKEN})
    assert _count(db_session, Account) == 1


def test_over_the_daily_cap_signup_is_503_and_creates_nothing(
    api_client: TestClient, db_session: Session, clock: _Clock, sent_mail: RecordingSender
) -> None:
    db_session.add_all(
        MailSend(purpose=EmailCodePurpose.VERIFY_EMAIL, created_at=NOW, updated_at=NOW)
        for _ in range(250)
    )
    db_session.commit()
    response = api_client.post("/auth/signup", json={"email": "ann@x.org", "password": PASSWORD})
    assert (response.status_code, response.json()) == (503, {"detail": MAIL_UNAVAILABLE})
    assert (_count(db_session, Account), _count(db_session, EmailCode)) == (0, 0)
    assert sent_mail.sent == []
    assert VERIFY_COOKIE_NAME not in api_client.cookies


def test_signup_counts_toward_the_ip_limit(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    for number in range(20):
        _signup(api_client, f"user{number}@x.org")
    response = api_client.post("/auth/signup", json={"email": "late@x.org", "password": PASSWORD})
    assert (response.status_code, response.json()) == (429, {"detail": TOO_MANY_LOGINS})
    assert int(response.headers["Retry-After"]) > 0
    assert _count(db_session, User) == 20


def test_verify_over_the_ip_limit_is_429_without_checking(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    code = code_in(sent_mail.sent[0])
    for _ in range(4):
        assert _verify(api_client, _wrong(code)) == 400
    clock.now = NOW + timedelta(minutes=1)
    for number in range(15):
        api_client.post("/auth/login", json={"email": f"x{number}@x.org", "password": WRONG})
    assert _verify(api_client, code) == 429


# --- a new code ---


def test_a_new_code_waits_a_minute_then_replaces_the_old_one(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    clock.now = NOW + timedelta(seconds=20)
    early = api_client.post(RESEND_PATH)
    assert (early.status_code, early.json()) == (429, {"detail": CODE_TOO_SOON})
    assert early.headers["Retry-After"] == "40"
    clock.now = NOW + timedelta(seconds=60)
    response = api_client.post(RESEND_PATH)
    assert (response.status_code, response.json()) == (202, {"detail": CODE_SENT})
    old, new = (code_in(message) for message in sent_mail.sent)
    if old != new:
        assert _verify(api_client, old) == 400
    assert _verify(api_client, new) == 200


def test_a_dead_code_keeps_the_older_one_dead_and_still_holds_the_gap(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    clock.now = NOW + timedelta(minutes=1)
    assert api_client.post(RESEND_PATH).status_code == 202
    old, new = (code_in(message) for message in sent_mail.sent)
    for _ in range(5):
        assert _verify(api_client, _wrong(new)) == 400
    assert _verify(api_client, old) == 400
    clock.now = NOW + timedelta(minutes=1, seconds=30)
    response = api_client.post(RESEND_PATH)
    assert (response.status_code, response.headers["Retry-After"]) == (429, "30")
    assert len(sent_mail.sent) == 2


def test_the_sixth_code_in_an_hour_is_429(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    for minutes in range(1, 5):
        clock.now = NOW + timedelta(minutes=minutes)
        assert api_client.post(RESEND_PATH).status_code == 202
    clock.now = NOW + timedelta(minutes=5)
    response = api_client.post(RESEND_PATH)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == str(55 * 60)
    assert len(sent_mail.sent) == 5


def test_over_the_daily_cap_a_new_code_is_503(
    api_client: TestClient, db_session: Session, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    db_session.add_all(
        MailSend(purpose=EmailCodePurpose.VERIFY_EMAIL, created_at=NOW, updated_at=NOW)
        for _ in range(249)
    )
    db_session.commit()
    clock.now = NOW + timedelta(minutes=1)
    response = api_client.post(RESEND_PATH)
    assert (response.status_code, response.json()) == (503, {"detail": MAIL_UNAVAILABLE})
    assert len(sent_mail.sent) == 1


def test_a_new_code_for_a_confirmed_email_is_409(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    cookie = api_client.cookies[VERIFY_COOKIE_NAME]
    assert _verify(api_client, code_in(sent_mail.sent[0])) == 200
    api_client.cookies.set(VERIFY_COOKIE_NAME, cookie, path=VERIFY_COOKIE_PATH)
    clock.now = NOW + timedelta(minutes=1)
    response = api_client.post(RESEND_PATH)
    assert (response.status_code, response.json()) == (409, {"detail": ALREADY_VERIFIED})


def test_a_new_code_for_a_replaced_user_is_401(api_client: TestClient, clock: _Clock) -> None:
    _signup(api_client)
    cookie = api_client.cookies[VERIFY_COOKIE_NAME]
    api_client.cookies.clear()
    _signup(api_client)
    api_client.cookies.clear()
    api_client.cookies.set(VERIFY_COOKIE_NAME, cookie, path=VERIFY_COOKIE_PATH)
    clock.now = NOW + timedelta(minutes=1)
    response = api_client.post(RESEND_PATH)
    assert (response.status_code, response.json()) == (401, {"detail": VERIFY_EXPIRED})


def test_a_new_code_over_the_ip_limit_is_429_without_an_email(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    for number in range(19):
        api_client.post("/auth/login", json={"email": f"x{number}@x.org", "password": WRONG})
    clock.now = NOW + timedelta(minutes=1)
    response = api_client.post(RESEND_PATH)
    assert (response.status_code, response.json()) == (429, {"detail": TOO_MANY_LOGINS})
    assert len(sent_mail.sent) == 1


# --- login while unverified ---


def test_the_right_password_of_an_unverified_email_is_403_with_a_new_code(
    api_client: TestClient, db_session: Session, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    clock.now = NOW + timedelta(minutes=2)
    response = api_client.post("/auth/login", json={"email": "ann@x.org", "password": PASSWORD})
    assert (response.status_code, response.json()) == (
        403,
        {"detail": EMAIL_NOT_VERIFIED, "code": EMAIL_NOT_VERIFIED_CODE},
    )
    assert AUTH_COOKIE_NAME not in api_client.cookies
    assert VERIFY_COOKIE_NAME in api_client.cookies
    assert len(sent_mail.sent) == 2
    assert _verify(api_client, code_in(sent_mail.sent[1])) == 200
    assert api_client.get("/me").status_code == 200


def test_a_login_within_the_gap_is_403_without_a_new_email(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    response = api_client.post("/auth/login", json={"email": "ann@x.org", "password": PASSWORD})
    assert response.status_code == 403
    assert len(sent_mail.sent) == 1
    # The first code still works through the new cookie.
    assert _verify(api_client, code_in(sent_mail.sent[0])) == 200


def test_a_wrong_password_of_an_unverified_email_is_the_usual_401(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    clock.now = NOW + timedelta(minutes=2)
    response = api_client.post("/auth/login", json={"email": "ann@x.org", "password": WRONG})
    assert (response.status_code, response.json()) == (401, {"detail": LOGIN_FAILED})
    assert VERIFY_COOKIE_NAME not in api_client.cookies
    assert len(sent_mail.sent) == 1


def test_nothing_secret_is_logged_or_answered(
    api_client: TestClient,
    clock: _Clock,
    sent_mail: RecordingSender,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    signup = api_client.post("/auth/signup", json={"email": "ann@x.org", "password": PASSWORD})
    code = code_in(sent_mail.sent[0])
    responses = [
        api_client.post(VERIFY_PATH, json={"code": _wrong(code)}),
        api_client.post(VERIFY_PATH, json={"code": code}),
    ]
    assert [response.status_code for response in responses] == [400, 200]
    shown = caplog.text + "".join(response.text for response in responses)
    assert "email verified: account" in caplog.text
    for secret in (code, _wrong(code), PASSWORD, "testclient"):
        assert secret not in shown
    assert "ann@x.org" not in caplog.text
    assert code not in signup.text and code not in str(dict(signup.headers))
