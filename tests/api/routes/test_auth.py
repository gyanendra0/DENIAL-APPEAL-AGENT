"""Sign-up, login and logout through the real app on `app_test`."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api import login as login_module
from src.api.dependencies import AUTH_COOKIE_NAME
from src.api.routes import auth as auth_module
from src.api.routes.auth import ACCOUNT_NAME_TAKEN, EMAIL_TAKEN, LOGIN_FAILED
from src.db.models import Account, User, UserRole
from tests.api.conftest import API_TTL_MINUTES

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)


def _signup(
    client: TestClient, name: str = "Clinic One", email: str = "Ann@X.org"
) -> dict[str, str]:
    response = client.post(
        "/auth/signup", json={"account_name": name, "email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    body: dict[str, str] = response.json()
    return body


def _counts(session: Session) -> tuple[int | None, int | None]:
    return (
        session.scalar(select(func.count()).select_from(Account)),
        session.scalar(select(func.count()).select_from(User)),
    )


def _set_cookie_header(client: TestClient, path: str, json: dict[str, str] | None) -> str:
    response = client.post(path, json=json)
    return response.headers["set-cookie"].lower()


def test_signup_creates_an_admin_and_logs_in(api_client: TestClient, db_session: Session) -> None:
    body = _signup(api_client)
    assert body["email"] == "ann@x.org"
    assert body["role"] == UserRole.ADMIN
    assert body["account_name"] == "Clinic One"
    assert AUTH_COOKIE_NAME in api_client.cookies
    assert api_client.get("/me").json()["user_id"] == body["user_id"]
    stored = db_session.scalars(select(User)).one()
    assert (stored.email, stored.role) == ("ann@x.org", UserRole.ADMIN)


@pytest.mark.parametrize(
    ("name", "email", "detail"),
    [("Clinic One", "other@x.org", ACCOUNT_NAME_TAKEN), ("Clinic Two", "ann@x.org", EMAIL_TAKEN)],
)
def test_signup_with_a_taken_name_or_email_is_409_and_creates_nothing(
    api_client: TestClient, db_session: Session, name: str, email: str, detail: str
) -> None:
    _signup(api_client)
    before = _counts(db_session)
    response = api_client.post(
        "/auth/signup", json={"account_name": name, "email": email, "password": PASSWORD}
    )
    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert _counts(db_session) == before


@pytest.mark.parametrize("length", [11, 129])
def test_signup_with_a_password_outside_12_to_128_is_422(
    api_client: TestClient, db_session: Session, length: int
) -> None:
    response = api_client.post(
        "/auth/signup", json={"account_name": "c", "email": "a@x.org", "password": "p" * length}
    )
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
            json={"account_name": "One", "email": "a@x.org", "password": PASSWORD},
        ),
        api_client.post("/auth/login", json={"email": "a@x.org", "password": PASSWORD}),
        api_client.get("/me"),
    ]
    password_hash = db_session.scalars(select(User.password_hash)).one()
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
