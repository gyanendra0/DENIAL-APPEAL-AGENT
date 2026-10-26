"""`GET /me` and `POST /me/password`: the logged-in user's own record and password, and 401
without a valid login."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.api.dependencies import AUTH_COOKIE_NAME, get_now
from src.api.login_limit import TOO_MANY_LOGINS
from src.api.routes import me as me_module
from src.api.routes.me import WRONG_CURRENT_PASSWORD
from src.api.schemas import MAX_LOGIN_PASSWORD_CHARACTERS
from src.db.models import AuditAction, AuditEvent, LoginFailure, LoginKeyKind, User
from src.db.query import for_account

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
NEW_PASSWORD = "a-new-made-up-password"  # noqa: S105  (made up)
WRONG = "a-made-up-wrong-password"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _signup(client: TestClient, name: str, email: str) -> dict[str, object]:
    response = client.post(
        "/auth/signup", json={"account_name": name, "email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def test_me_returns_the_user_and_the_account(api_client: TestClient) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    response = api_client.get("/me")
    assert response.status_code == 200
    assert response.json() == signed_up
    assert set(signed_up) == {"user_id", "email", "role", "account_id", "account_name"}


def test_user_of_account_a_sees_only_account_a(api_client: TestClient) -> None:
    b = _signup(api_client, "B", "b@x.org")
    api_client.cookies.clear()
    a = _signup(api_client, "A", "a@x.org")
    me = api_client.get("/me").json()
    assert (me["account_id"], me["account_name"]) == (a["account_id"], "A")
    assert me["account_id"] != b["account_id"]
    assert "b@x.org" not in str(me)


def test_no_cookie_is_401(api_client: TestClient) -> None:
    assert api_client.get("/me").status_code == 401


def test_damaged_cookie_is_401(api_client: TestClient) -> None:
    api_client.cookies.set(AUTH_COOKIE_NAME, "not-a-token")
    assert api_client.get("/me").status_code == 401


def test_deleted_user_is_401(api_client: TestClient, db_session: Session) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    # Sign-up audited the user, and an audited user cannot be deleted alone.
    db_session.execute(delete(AuditEvent).where(AuditEvent.user_id == signed_up["user_id"]))
    db_session.execute(delete(User).where(User.id == signed_up["user_id"]))
    db_session.commit()
    assert api_client.get("/me").status_code == 401


def test_user_deleted_after_the_login_check_is_401(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _signup(api_client, "One", "a@x.org")
    monkeypatch.setattr(me_module, "find_user_response", lambda *_, **__: None)
    assert api_client.get("/me").status_code == 401


# --- POST /me/password ---


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


def _change(client: TestClient, current: str = PASSWORD, new: str = NEW_PASSWORD) -> int:
    response = client.post("/me/password", json={"current_password": current, "new_password": new})
    return response.status_code


def _login(client: TestClient, password: str, email: str = "a@x.org") -> int:
    return client.post("/auth/login", json={"email": email, "password": password}).status_code


def _hash(session: Session, user_id: object) -> str:
    session.expire_all()
    found = session.scalar(select(User.password_hash).where(User.id == user_id))
    assert found is not None
    return found


def _password_events(session: Session) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.action == AuditAction.PASSWORD_CHANGED)
        ).all()
    )


def test_change_password_makes_the_new_password_the_only_one(
    api_client: TestClient, clock: _Clock
) -> None:
    _signup(api_client, "One", "a@x.org")
    response = api_client.post(
        "/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert (response.status_code, response.content) == (204, b"")
    api_client.cookies.clear()
    assert _login(api_client, PASSWORD) == 401
    assert _login(api_client, NEW_PASSWORD) == 200


def test_a_wrong_current_password_is_400_and_changes_nothing(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    before = _hash(db_session, signed_up["user_id"])
    response = api_client.post(
        "/me/password", json={"current_password": WRONG, "new_password": NEW_PASSWORD}
    )
    assert (response.status_code, response.json()) == (400, {"detail": WRONG_CURRENT_PASSWORD})
    assert WRONG not in response.text and NEW_PASSWORD not in response.text
    assert _hash(db_session, signed_up["user_id"]) == before
    assert _password_events(db_session) == []
    assert api_client.get("/me").status_code == 200


@pytest.mark.parametrize("length", [11, 129])
def test_a_new_password_outside_12_to_128_is_422_and_changes_nothing(
    api_client: TestClient, db_session: Session, clock: _Clock, length: int
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    before = _hash(db_session, signed_up["user_id"])
    response = api_client.post(
        "/me/password", json={"current_password": PASSWORD, "new_password": "n" * length}
    )
    assert response.status_code == 422
    assert "n" * length not in response.text and PASSWORD not in response.text
    assert _hash(db_session, signed_up["user_id"]) == before
    # Refused before the route runs, so it is not counted toward the login limit.
    assert db_session.scalars(select(LoginFailure)).all() == []


def test_change_password_without_a_login_is_401(api_client: TestClient, clock: _Clock) -> None:
    assert _change(api_client) == 401


def test_the_user_cannot_be_named_in_the_body(api_client: TestClient, clock: _Clock) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    response = api_client.post(
        "/me/password",
        json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "user_id": signed_up["user_id"],
        },
    )
    assert response.status_code == 422


def test_change_password_changes_only_the_logged_in_user(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    other = _signup(api_client, "B", "b@x.org")
    other_before = _hash(db_session, other["user_id"])
    api_client.cookies.clear()
    _signup(api_client, "A", "a@x.org")
    assert _change(api_client) == 204
    assert _hash(db_session, other["user_id"]) == other_before


def test_older_tokens_stop_working_and_this_session_stays(
    api_client: TestClient, clock: _Clock
) -> None:
    _signup(api_client, "One", "a@x.org")
    old_token = api_client.cookies[AUTH_COOKIE_NAME]
    assert _change(api_client) == 204
    new_token = api_client.cookies[AUTH_COOKIE_NAME]
    assert new_token != old_token
    assert api_client.get("/me").status_code == 200
    # Another browser still holding a token from before the change (and only that token).
    api_client.cookies.clear()
    api_client.cookies.set(AUTH_COOKIE_NAME, old_token)
    assert api_client.get("/me").status_code == 401


def test_change_password_writes_one_audit_event_without_a_job_or_secret(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    assert _change(api_client) == 204
    events = _password_events(db_session)
    assert len(events) == 1
    event = events[0]
    assert (event.account_id, event.user_id) == (signed_up["account_id"], signed_up["user_id"])
    assert (event.job_id, event.detail) == (None, {})
    assert api_client.get("/audit").json()["items"][0]["action"] == "password_changed"


def test_five_wrong_current_passwords_then_even_the_right_one_is_429(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    signed_up = _signup(api_client, "One", "a@x.org")
    before = _hash(db_session, signed_up["user_id"])
    for _ in range(5):
        assert _change(api_client, current=WRONG) == 400
    clock.now = NOW + timedelta(minutes=1)
    response = api_client.post(
        "/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert (response.status_code, response.json()) == (429, {"detail": TOO_MANY_LOGINS})
    assert response.headers["retry-after"] == str(14 * 60)
    assert _hash(db_session, signed_up["user_id"]) == before


def test_wrong_current_passwords_count_toward_the_login_limit(
    api_client: TestClient, clock: _Clock
) -> None:
    _signup(api_client, "One", "a@x.org")
    for _ in range(5):
        assert _change(api_client, current=WRONG) == 400
    api_client.cookies.clear()
    assert _login(api_client, PASSWORD) == 429


def test_failed_logins_count_toward_the_change_limit(api_client: TestClient, clock: _Clock) -> None:
    _signup(api_client, "One", "a@x.org")
    cookie = api_client.cookies[AUTH_COOKIE_NAME]
    api_client.cookies.clear()
    for _ in range(5):
        assert _login(api_client, WRONG) == 401
    api_client.cookies.set(AUTH_COOKIE_NAME, cookie)
    assert _change(api_client) == 429


def test_a_change_clears_the_email_count_but_not_the_ip_count(
    api_client: TestClient, db_session: Session, clock: _Clock
) -> None:
    _signup(api_client, "One", "a@x.org")
    for _ in range(4):
        assert _change(api_client, current=WRONG) == 400
    assert _change(api_client) == 204
    kinds = db_session.scalars(select(LoginFailure.key_kind)).all()
    assert list(kinds) == [LoginKeyKind.IP] * 4


def test_a_current_password_over_the_cap_is_400_without_hashing(
    api_client: TestClient, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _signup(api_client, "One", "a@x.org")
    checked: list[str] = []
    monkeypatch.setattr(me_module, "verify_password", lambda _hash, pw: checked.append(pw))
    assert _change(api_client, current="p" * (MAX_LOGIN_PASSWORD_CHARACTERS + 1)) == 400
    assert checked == []


def test_user_deleted_before_the_change_is_401(
    api_client: TestClient, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _signup(api_client, "One", "a@x.org")

    # The row lookup inside the route finds nothing, as if the user was deleted meanwhile.
    monkeypatch.setattr(me_module, "for_account", lambda model, _account_id: for_account(model, -1))
    assert _change(api_client) == 401


def test_change_password_never_logs_or_answers_a_password(
    api_client: TestClient, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    _signup(api_client, "One", "a@x.org")
    caplog.set_level(logging.INFO)
    responses = [
        api_client.post(
            "/me/password", json={"current_password": current, "new_password": NEW_PASSWORD}
        )
        for current in (WRONG, PASSWORD)
    ]
    assert [response.status_code for response in responses] == [400, 204]
    shown = caplog.text + "".join(
        response.text + str(dict(response.headers)) for response in responses
    )
    assert "password changed: account" in caplog.text
    assert "password change failed" in caplog.text
    for secret in ("a@x.org", WRONG, PASSWORD, NEW_PASSWORD, "testclient"):
        assert secret not in shown
