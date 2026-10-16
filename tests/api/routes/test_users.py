"""`GET /users/{user_id}`: admin only, and another account's user is 404, not 403."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.api.passwords import hash_password
from src.api.routes.users import USER_NOT_FOUND
from src.db.models import User, UserRole

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)


def _signup(client: TestClient, name: str, email: str) -> dict[str, int]:
    response = client.post(
        "/auth/signup", json={"account_name": name, "email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    body: dict[str, int] = response.json()
    return body


def _add_user(session: Session, account_id: int, email: str, role: UserRole) -> User:
    user = User(
        account_id=account_id, email=email, password_hash=hash_password(PASSWORD), role=role
    )
    session.add(user)
    session.commit()
    return user


def _login(client: TestClient, email: str) -> None:
    client.cookies.clear()
    assert (
        client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    )


def test_admin_reads_a_user_of_the_own_account(api_client: TestClient, db_session: Session) -> None:
    admin = _signup(api_client, "A", "admin@a.org")
    viewer = _add_user(db_session, admin["account_id"], "viewer@a.org", UserRole.VIEWER)
    response = api_client.get(f"/users/{viewer.id}")
    assert response.status_code == 200
    assert (response.json()["email"], response.json()["role"]) == ("viewer@a.org", "viewer")


def test_user_of_another_account_is_404_like_a_missing_one(api_client: TestClient) -> None:
    b = _signup(api_client, "B", "admin@b.org")
    api_client.cookies.clear()
    a = _signup(api_client, "A", "admin@a.org")
    other = api_client.get(f"/users/{b['user_id']}")
    missing = api_client.get(f"/users/{a['user_id'] + b['user_id'] + 1000}")
    assert (other.status_code, other.json()) == (404, {"detail": USER_NOT_FOUND})
    assert (missing.status_code, missing.json()) == (404, {"detail": USER_NOT_FOUND})


def test_a_role_other_than_admin_is_403(api_client: TestClient, db_session: Session) -> None:
    admin = _signup(api_client, "A", "admin@a.org")
    for email, role in (("viewer@a.org", UserRole.VIEWER), ("reviewer@a.org", UserRole.REVIEWER)):
        _add_user(db_session, admin["account_id"], email, role)
        _login(api_client, email)
        assert api_client.get(f"/users/{admin['user_id']}").status_code == 403


def test_without_login_is_401(api_client: TestClient) -> None:
    assert api_client.get("/users/1").status_code == 401
