"""`POST /auth/password-reset` and `/confirm`: the same answer for every email, one working code
per user, every session ended by a reset, and the IP limit on both routes."""

import logging
import smtplib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from src.api.dependencies import get_now
from src.api.email_codes import CODE_INVALID
from src.api.login_limit import TOO_MANY_LOGINS
from src.api.mail import CODE_SUBJECTS, MailMessage
from src.api.routes.password_reset import RESET_ASKED
from src.db.models import AuditAction, AuditEvent, EmailCode, EmailCodePurpose, MailSend, User
from tests.api.conftest import RecordingSender, code_in, signup_and_verify

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


def _confirm(
    client: TestClient, code: str, password: str = NEW_PASSWORD, email: str = "a@x.org"
) -> int:
    body = {"email": email, "code": code, "new_password": password}
    return client.post("/auth/password-reset/confirm", json=body).status_code


def _other(code: str) -> str:
    """A code that is surely not `code`."""
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _login(client: TestClient, password: str, email: str = "a@x.org") -> int:
    return client.post("/auth/login", json={"email": email, "password": password}).status_code


def _code_rows(session: Session) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(EmailCode)) or 0


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


def test_the_email_carries_a_reset_code_and_no_link_and_only_its_keyed_hash_is_stored(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    _signup(api_client)
    _ask(api_client)
    [message] = sent_mail.sent
    code = code_in(message)
    assert message.subject == CODE_SUBJECTS[EmailCodePurpose.PASSWORD_RESET]
    assert code not in message.subject
    assert "http" not in message.body
    [row] = db_session.scalars(select(EmailCode)).all()
    assert row.purpose is EmailCodePurpose.PASSWORD_RESET
    assert row.expires_at == NOW + timedelta(minutes=10)
    assert code not in row.code_hash


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


def test_a_dead_code_still_counts_toward_the_hourly_cap(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    _ask(api_client)
    code = code_in(sent_mail.sent[1])
    assert [_confirm(api_client, _other(code)) for _ in range(5)] == [400] * 5
    _ask(api_client)
    assert _ask(api_client) == (202, {"detail": RESET_ASKED})
    assert len(sent_mail.sent) == 3


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


def test_over_the_daily_cap_the_answer_is_the_same_202_and_nothing_is_sent_or_stored(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    _signup(api_client)
    codes_before = _code_rows(db_session)
    db_session.add_all(
        MailSend(purpose=EmailCodePurpose.VERIFY_EMAIL, created_at=NOW, updated_at=NOW)
        for _ in range(250)
    )
    db_session.commit()
    assert _ask(api_client) == (202, {"detail": RESET_ASKED})
    assert sent_mail.sent == []
    assert _code_rows(db_session) == codes_before


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
        json={"email": "a@x.org", "code": code_in(sent_mail.sent[0]), "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 204
    assert "set-cookie" not in response.headers
    # The client still sends the login cookie, issued before the reset.
    assert api_client.get("/me").status_code == 401
    assert _login(api_client, PASSWORD) == 401
    assert _login(api_client, NEW_PASSWORD) == 200


def test_a_code_works_once(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    _signup(api_client)
    _ask(api_client)
    code = code_in(sent_mail.sent[0])
    assert _confirm(api_client, code) == 204
    assert _code_rows(db_session) == 0
    response = api_client.post(
        "/auth/password-reset/confirm",
        json={"email": "a@x.org", "code": code, "new_password": "yet-another-one"},
    )
    assert (response.status_code, response.json()) == (400, {"detail": CODE_INVALID})
    assert _login(api_client, NEW_PASSWORD) == 200


def test_an_expired_code_is_400_and_changes_nothing(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    clock.now += timedelta(minutes=10)
    assert _confirm(api_client, code_in(sent_mail.sent[0])) == 400
    assert _login(api_client, PASSWORD) == 200


def test_an_older_code_is_400_once_a_newer_one_was_asked(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    clock.now += timedelta(minutes=1)
    _ask(api_client)
    older, newer = (code_in(message) for message in sent_mail.sent)
    if older != newer:
        assert _confirm(api_client, older) == 400
    assert _confirm(api_client, newer) == 204


def test_five_wrong_codes_kill_the_code_so_even_the_right_one_is_400(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    _signup(api_client)
    _ask(api_client)
    code = code_in(sent_mail.sent[0])
    assert [_confirm(api_client, _other(code)) for _ in range(4)] == [400] * 4
    [row] = db_session.scalars(select(EmailCode)).all()
    assert row.wrong_tries == 4
    assert _confirm(api_client, _other(code)) == 400
    assert _code_rows(db_session) == 1
    assert _confirm(api_client, code) == 400
    assert _login(api_client, PASSWORD) == 200


def test_an_unknown_email_gets_the_same_400_as_a_wrong_code(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    code = code_in(sent_mail.sent[0])
    body = {"code": code, "new_password": NEW_PASSWORD}
    unknown = api_client.post("/auth/password-reset/confirm", json=body | {"email": "nobody@x.org"})
    wrong = api_client.post(
        "/auth/password-reset/confirm", json=body | {"email": "a@x.org", "code": _other(code)}
    )
    assert (unknown.status_code, unknown.json()) == (wrong.status_code, wrong.json())
    assert unknown.status_code == 400
    assert _confirm(api_client, code) == 204


def test_a_code_of_another_email_is_400(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    api_client.cookies.clear()
    _signup(api_client, "Two", "b@x.org")
    _ask(api_client, "a@x.org")
    assert _confirm(api_client, code_in(sent_mail.sent[0]), email="b@x.org") == 400
    assert _login(api_client, PASSWORD, "b@x.org") == 200


def test_a_sign_up_code_is_not_a_reset_code(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    body = {"account_name": "One", "email": "a@x.org", "password": PASSWORD}
    assert api_client.post("/auth/signup", json=body).status_code == 201
    assert _confirm(api_client, code_in(sent_mail.sent[0])) == 400


def test_a_password_change_makes_an_asked_code_useless(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender
) -> None:
    _signup(api_client)
    _ask(api_client)
    change = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert api_client.post("/me/password", json=change).status_code == 204
    assert _confirm(api_client, code_in(sent_mail.sent[0]), "an-attackers-password") == 400
    assert _login(api_client, NEW_PASSWORD) == 200


def test_a_code_of_a_deleted_user_is_400(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    user_id = _signup(api_client)
    _ask(api_client)
    db_session.execute(delete(AuditEvent).where(AuditEvent.user_id == user_id))
    db_session.execute(delete(User).where(User.id == user_id))
    db_session.commit()
    assert _code_rows(db_session) == 0
    assert _confirm(api_client, code_in(sent_mail.sent[0])) == 400


@pytest.mark.parametrize("length", [11, 129])
def test_a_new_password_outside_12_to_128_is_422_and_the_code_still_works(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, length: int
) -> None:
    _signup(api_client)
    _ask(api_client)
    code = code_in(sent_mail.sent[0])
    assert _confirm(api_client, code, "x" * length) == 422
    assert _confirm(api_client, code) == 204


@pytest.mark.parametrize(
    "change",
    [{"code": "12345"}, {"code": "1234567"}, {"code": "12345a"}, {"email": "no"}, {"user_id": 1}],
    ids=["short-code", "long-code", "letter", "bad-email", "extra-field"],
)
def test_a_bad_code_or_email_or_an_extra_field_is_422(
    api_client: TestClient, clock: _Clock, change: dict[str, object]
) -> None:
    body = {"email": "a@x.org", "code": "123456", "new_password": NEW_PASSWORD} | change
    assert api_client.post("/auth/password-reset/confirm", json=body).status_code == 422


def test_a_reset_writes_one_audit_event_without_a_job(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    user_id = _signup(api_client)
    _ask(api_client)
    _confirm(api_client, code_in(sent_mail.sent[0]))
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
    assert _confirm(api_client, code_in(sent_mail.sent[0])) == 204
    assert _login(api_client, NEW_PASSWORD) == 200


def test_a_reset_confirms_an_unverified_email_and_kills_its_sign_up_code(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    body = {"account_name": "One", "email": "a@x.org", "password": PASSWORD}
    assert api_client.post("/auth/signup", json=body).status_code == 201
    signup_code = code_in(sent_mail.sent[0])
    _ask(api_client)
    assert _confirm(api_client, code_in(sent_mail.sent[1])) == 204
    user = db_session.scalars(select(User).where(User.email == "a@x.org")).one()
    db_session.refresh(user)
    assert user.email_verified_at == NOW
    assert _code_rows(db_session) == 0
    assert api_client.post("/auth/verify-email", json={"code": signup_code}).status_code == 400
    assert _login(api_client, NEW_PASSWORD) == 200
    # No `email_verified` row: the reset audits itself only.
    actions = db_session.scalars(select(AuditEvent.action)).all()
    assert AuditAction.EMAIL_VERIFIED not in actions


def test_a_user_with_no_password_sets_a_first_one(
    api_client: TestClient, clock: _Clock, sent_mail: RecordingSender, db_session: Session
) -> None:
    user_id = _signup(api_client)
    db_session.execute(update(User).where(User.id == user_id).values(password_hash=None))
    db_session.commit()
    _ask(api_client)
    assert _confirm(api_client, code_in(sent_mail.sent[0])) == 204
    assert _login(api_client, NEW_PASSWORD) == 200


def test_failed_confirms_count_toward_the_ip_limit(api_client: TestClient, clock: _Clock) -> None:
    assert [_confirm(api_client, "123456") for _ in range(20)] == [400] * 20
    response = api_client.post(
        "/auth/password-reset/confirm",
        json={"email": "a@x.org", "code": "123456", "new_password": NEW_PASSWORD},
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
    code = code_in(sent_mail.sent[0])
    for sent_code in (_other(code), code):
        responses.append(
            api_client.post(
                "/auth/password-reset/confirm",
                json={"email": "a@x.org", "code": sent_code, "new_password": NEW_PASSWORD},
            )
        )
    assert [response.status_code for response in responses] == [202, 202, 400, 204]
    shown = caplog.text + "".join(
        response.text + str(dict(response.headers)) for response in responses
    )
    assert "password reset: account" in caplog.text
    for secret in ("a@x.org", "nobody@x.org", code, _other(code), NEW_PASSWORD, "testclient"):
        assert secret not in shown
