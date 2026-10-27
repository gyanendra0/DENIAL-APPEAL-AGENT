"""`POST /auth/password-reset` and `/confirm`: the same answer for every email, one working link
per user, every session ended by a reset, and the IP limit on both routes."""

import logging
import smtplib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.api.dependencies import AUTH_COOKIE_NAME, get_now
from src.api.login_limit import TOO_MANY_LOGINS
from src.api.mail import MailMessage
from src.api.routes.password_reset import RESET_ASKED, RESET_LINK_INVALID, RESET_PATH
from src.db.models import AuditAction, AuditEvent, PasswordResetToken, User
from tests.api.conftest import PUBLIC_URL, RecordingSender, signup_and_verify

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
NEW_PASSWORD = "a-new-made-up-password"  # noqa: S105  (made up)
WRONG = "a-made-up-wrong-password"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


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


def _signup(client: TestClient, name: str = "One", email: str = "a@x.org") -> int:
    signed_up = signup_and_verify(client, name=name, email=email, password=PASSWORD)
    user_id: int = signed_up["user_id"]
    return user_id


def _ask(client: TestClient, email: str = "a@x.org") -> tuple[int, object]:
    response = client.post("/auth/password-reset", json={"email": email})
    return response.status_code, response.json()


def _token(message: MailMessage) -> str:
    return message.body.split("#token=", 1)[1].split("\n", 1)[0]


def _confirm(client: TestClient, token: str, password: str = NEW_PASSWORD) -> int:
    response = client.post(
        "/auth/password-reset/confirm", json={"token": token, "new_password": password}
    )
    return response.status_code


def _login(client: TestClient, password: str, email: str = "a@x.org") -> int:
    return client.post("/auth/login", json={"email": email, "password": password}).status_code


def _token_rows(session: Session) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(PasswordResetToken)) or 0


# --- asking ---


def test_known_and_unknown_emails_get_the_same_answer_and_only_the_known_one_an_email(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    known = _ask(api_client, "a@x.org")
    unknown = _ask(api_client, "nobody@x.org")
    assert known == unknown == (202, {"detail": RESET_ASKED})
    assert [message.to for message in sent_mail.sent] == ["a@x.org"]


def test_the_email_is_trimmed_and_lowercased_like_at_login(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    assert _ask(api_client, "  A@X.org ")[0] == 202
    assert [message.to for message in sent_mail.sent] == ["a@x.org"]


def test_the_link_points_at_the_public_url_with_the_token_in_the_fragment(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    [message] = sent_mail.sent
    assert f"{PUBLIC_URL}{RESET_PATH}{_token(message)}\n" in message.body
    assert "testserver" not in message.body


def test_a_fourth_ask_within_the_hour_is_the_same_202_without_an_email(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    answers = [_ask(api_client) for _ in range(4)]
    assert answers == [(202, {"detail": RESET_ASKED})] * 4
    assert len(sent_mail.sent) == 3
    clock.now += timedelta(minutes=61)
    _ask(api_client)
    assert len(sent_mail.sent) == 4


def test_the_cap_counts_one_email_only(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    _signup(api_client, "Two", "b@x.org")
    for _ in range(3):
        _ask(api_client, "a@x.org")
    _ask(api_client, "b@x.org")
    assert [message.to for message in sent_mail.sent][-1] == "b@x.org"


def test_asking_over_the_ip_limit_is_429_with_a_wait(api_client: TestClient, clock: _Clock) -> None:
    for number in range(20):
        assert _ask(api_client, f"nobody{number}@x.org")[0] == 202
    response = api_client.post("/auth/password-reset", json={"email": "a@x.org"})
    assert (response.status_code, response.json()) == (429, {"detail": TOO_MANY_LOGINS})
    assert response.headers["Retry-After"] == str(15 * 60)


def test_a_bad_email_or_an_extra_field_is_422(api_client: TestClient, clock: _Clock) -> None:
    assert (
        api_client.post("/auth/password-reset", json={"email": "not-an-email"}).status_code == 422
    )
    body = {"email": "a@x.org", "account_id": 1}
    assert api_client.post("/auth/password-reset", json=body).status_code == 422


def test_a_failed_send_still_answers_202_and_logs_no_address(
    api_client: TestClient,
    clock: _Clock,
    sent_mail: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _signup(api_client)

    def refuse(message: MailMessage) -> None:
        raise smtplib.SMTPServerDisconnected("made up")

    monkeypatch.setattr(sent_mail, "send", refuse)
    caplog.set_level(logging.INFO)
    assert _ask(api_client) == (202, {"detail": RESET_ASKED})
    assert "email failed: SMTPServerDisconnected" in caplog.text
    assert "a@x.org" not in caplog.text


# --- confirming ---


def test_a_reset_sets_the_new_password_and_ends_every_session(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    response = api_client.post(
        "/auth/password-reset/confirm",
        json={"token": _token(sent_mail.sent[0]), "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 204
    assert "set-cookie" not in response.headers
    # The client still sends the sign-up cookie, issued before the reset.
    assert api_client.get("/me").status_code == 401
    assert _login(api_client, PASSWORD) == 401
    assert _login(api_client, NEW_PASSWORD) == 200


def test_a_link_works_once(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    _signup(api_client)
    _ask(api_client)
    token = _token(sent_mail.sent[0])
    assert _confirm(api_client, token) == 204
    assert _token_rows(db_session) == 0
    response = api_client.post(
        "/auth/password-reset/confirm", json={"token": token, "new_password": "yet-another-one"}
    )
    assert (response.status_code, response.json()) == (400, {"detail": RESET_LINK_INVALID})
    assert _login(api_client, NEW_PASSWORD) == 200


def test_an_expired_link_is_400_and_changes_nothing(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    clock.now += timedelta(minutes=30)
    assert _confirm(api_client, _token(sent_mail.sent[0])) == 400
    assert _login(api_client, PASSWORD) == 200


def test_an_older_link_is_400_once_a_newer_one_was_asked(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    clock.now += timedelta(minutes=1)
    _ask(api_client)
    older, newer = (_token(message) for message in sent_mail.sent)
    assert _confirm(api_client, older) == 400
    assert _confirm(api_client, newer) == 204


@pytest.mark.parametrize("kind", ["made-up", "login-token"])
def test_a_made_up_token_or_a_login_token_is_400(
    api_client: TestClient, clock: _Clock, kind: str
) -> None:
    _signup(api_client)
    token = "not.a.token" if kind == "made-up" else api_client.cookies.get(AUTH_COOKIE_NAME)
    assert token is not None
    assert _confirm(api_client, token) == 400
    assert _login(api_client, PASSWORD) == 200


def test_a_password_change_makes_an_asked_link_useless(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert api_client.post("/me/password", json=change).status_code == 204
    assert _confirm(api_client, _token(sent_mail.sent[0]), "an-attackers-password") == 400
    assert _login(api_client, NEW_PASSWORD) == 200


def test_a_link_of_a_deleted_user_is_400(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    user_id = _signup(api_client)
    _ask(api_client)
    db_session.execute(delete(AuditEvent).where(AuditEvent.user_id == user_id))
    db_session.execute(delete(User).where(User.id == user_id))
    db_session.commit()
    assert _token_rows(db_session) == 0
    assert _confirm(api_client, _token(sent_mail.sent[0])) == 400


@pytest.mark.parametrize("length", [11, 129])
def test_a_new_password_outside_12_to_128_is_422_and_the_link_still_works(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, length: int
) -> None:
    _signup(api_client)
    _ask(api_client)
    token = _token(sent_mail.sent[0])
    assert _confirm(api_client, token, "x" * length) == 422
    assert _confirm(api_client, token) == 204


def test_a_huge_token_or_an_extra_field_is_422(api_client: TestClient, clock: _Clock) -> None:
    assert _confirm(api_client, "x" * 2049) == 422
    body = {"token": "x", "new_password": NEW_PASSWORD, "user_id": 1}
    assert api_client.post("/auth/password-reset/confirm", json=body).status_code == 422


def test_a_reset_writes_one_audit_event_without_a_job(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    user_id = _signup(api_client)
    _ask(api_client)
    _confirm(api_client, _token(sent_mail.sent[0]))
    db_session.expire_all()
    [event] = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == AuditAction.PASSWORD_RESET)
    ).all()
    assert (event.user_id, event.job_id, event.detail) == (user_id, None, {})


def test_a_reset_lifts_the_login_limit_of_the_email(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    assert [_login(api_client, WRONG) for _ in range(6)] == [401] * 5 + [429]
    _ask(api_client)
    assert _confirm(api_client, _token(sent_mail.sent[0])) == 204
    assert _login(api_client, NEW_PASSWORD) == 200


def test_failed_confirms_count_toward_the_ip_limit(api_client: TestClient, clock: _Clock) -> None:
    assert [_confirm(api_client, "not.a.token") for _ in range(20)] == [400] * 20
    response = api_client.post(
        "/auth/password-reset/confirm", json={"token": "x", "new_password": NEW_PASSWORD}
    )
    assert (response.status_code, response.json()) == (429, {"detail": TOO_MANY_LOGINS})
    assert int(response.headers["Retry-After"]) >= 1


def test_nothing_secret_is_logged_or_answered(
    api_client: TestClient,
    clock: _Clock,
    sent_mail: RecordingSender,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _signup(api_client)
    caplog.set_level(logging.INFO)
    responses = [
        api_client.post("/auth/password-reset", json={"email": email})
        for email in ("a@x.org", "nobody@x.org")
    ]
    token = _token(sent_mail.sent[0])
    for sent_token in ("not.a.token", token):
        responses.append(
            api_client.post(
                "/auth/password-reset/confirm",
                json={"token": sent_token, "new_password": NEW_PASSWORD},
            )
        )
    assert [response.status_code for response in responses] == [202, 202, 400, 204]
    shown = caplog.text + "".join(
        response.text + str(dict(response.headers)) for response in responses
    )
    assert "password reset: account" in caplog.text
    for secret in ("a@x.org", "nobody@x.org", token, NEW_PASSWORD, PUBLIC_URL, "testclient"):
        assert secret not in shown
