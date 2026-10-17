"""`GET /me`: the logged-in user's own record, and 401 without a valid login."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.api.dependencies import AUTH_COOKIE_NAME
from src.api.routes import me as me_module
from src.db.models import AuditEvent, User

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)


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
