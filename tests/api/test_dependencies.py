"""The login and role dependencies, on a tiny app built here (the real app comes later)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from src.api.dependencies import (
    AUTH_COOKIE_NAME,
    NOT_ALLOWED,
    NOT_LOGGED_IN,
    CurrentUser,
    current_user,
    get_db_session,
    require_role,
)
from src.api.tokens import issue_token
from src.config.settings import ApiSettings
from src.db.models import Account, User, UserRole
from src.db.session import create_session_factory

SECRET = "a-made-up-test-secret-of-forty-characters"  # noqa: S105  (made up, not a real secret)
OTHER_SECRET = "another-made-up-secret-of-forty-characters"  # noqa: S105  (made up)
FAKE_HASH = "not-used-here"  # noqa: S105  (these tests never check a password)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.api_settings = ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(SECRET), _env_file=None
    )

    @app.get("/whoami")
    def whoami(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        return user

    @app.get("/admin-only")
    def admin_only(
        user: Annotated[CurrentUser, Depends(require_role(UserRole.ADMIN))],
    ) -> CurrentUser:
        return user

    @app.get("/reviewers")
    def reviewers(
        user: Annotated[CurrentUser, Depends(require_role(UserRole.REVIEWER, UserRole.ADMIN))],
    ) -> CurrentUser:
        return user

    return app


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = _build_app()

    def _session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db_session] = _session
    with TestClient(app) as test_client:
        yield test_client


def _user(session: Session, account_name: str, email: str, role: UserRole) -> User:
    account = Account(name=account_name)
    session.add(account)
    session.flush()
    user = User(account_id=account.id, email=email, password_hash=FAKE_HASH, role=role)
    session.add(user)
    session.flush()
    return user


def _login(
    client: TestClient,
    *,
    user_id: int,
    account_id: int,
    now: datetime | None = None,
    secret: str = SECRET,
) -> None:
    token = issue_token(
        user_id=user_id,
        account_id=account_id,
        secret=secret,
        ttl_minutes=60,
        now=now or datetime.now(UTC),
    )
    client.cookies.set(AUTH_COOKIE_NAME, token)


def test_valid_cookie_gives_the_current_user(client: TestClient, session: Session) -> None:
    user = _user(session, "Clinic A", "a@example.com", UserRole.REVIEWER)
    _login(client, user_id=user.id, account_id=user.account_id)

    response = client.get("/whoami")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": user.id,
        "account_id": user.account_id,
        "email": "a@example.com",
        "role": "reviewer",
    }


def test_no_cookie_is_401(client: TestClient) -> None:
    response = client.get("/whoami")
    assert response.status_code == 401
    assert response.json() == {"detail": NOT_LOGGED_IN}


def test_damaged_cookie_is_401(client: TestClient) -> None:
    client.cookies.set(AUTH_COOKIE_NAME, "junk")
    response = client.get("/whoami")
    assert response.status_code == 401
    assert response.json() == {"detail": NOT_LOGGED_IN}


def test_expired_token_is_401(client: TestClient, session: Session) -> None:
    user = _user(session, "Clinic A", "a@example.com", UserRole.ADMIN)
    _login(
        client,
        user_id=user.id,
        account_id=user.account_id,
        now=datetime.now(UTC) - timedelta(minutes=61),
    )
    assert client.get("/whoami").status_code == 401


def test_token_signed_with_another_secret_is_401(client: TestClient, session: Session) -> None:
    user = _user(session, "Clinic A", "a@example.com", UserRole.ADMIN)
    _login(
        client,
        user_id=user.id,
        account_id=user.account_id,
        secret=OTHER_SECRET,
    )
    assert client.get("/whoami").status_code == 401


def test_deleted_user_is_401(client: TestClient, session: Session) -> None:
    user = _user(session, "Clinic A", "a@example.com", UserRole.ADMIN)
    _login(client, user_id=user.id, account_id=user.account_id)
    session.delete(user)
    session.flush()

    response = client.get("/whoami")

    assert response.status_code == 401
    assert response.json() == {"detail": NOT_LOGGED_IN}


def test_token_naming_another_account_is_401(client: TestClient, session: Session) -> None:
    user_a = _user(session, "Clinic A", "a@example.com", UserRole.ADMIN)
    user_b = _user(session, "Clinic B", "b@example.com", UserRole.ADMIN)
    # User A's id with account B: the lookup inside account B finds no such user.
    _login(client, user_id=user_a.id, account_id=user_b.account_id)

    response = client.get("/whoami")

    assert response.status_code == 401
    assert response.json() == {"detail": NOT_LOGGED_IN}


def test_user_of_account_a_sees_only_account_a(client: TestClient, session: Session) -> None:
    user_a = _user(session, "Clinic A", "a@example.com", UserRole.VIEWER)
    _user(session, "Clinic B", "b@example.com", UserRole.ADMIN)
    _login(client, user_id=user_a.id, account_id=user_a.account_id)

    body = client.get("/whoami").json()

    assert body["account_id"] == user_a.account_id
    assert body["email"] == "a@example.com"


def test_role_named_passes(client: TestClient, session: Session) -> None:
    user = _user(session, "Clinic A", "a@example.com", UserRole.ADMIN)
    _login(client, user_id=user.id, account_id=user.account_id)
    assert client.get("/admin-only").status_code == 200
    assert client.get("/reviewers").status_code == 200


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.REVIEWER])
def test_role_not_named_is_403(client: TestClient, session: Session, role: UserRole) -> None:
    user = _user(session, "Clinic A", "a@example.com", role)
    _login(client, user_id=user.id, account_id=user.account_id)

    response = client.get("/admin-only")

    assert response.status_code == 403
    assert response.json() == {"detail": NOT_ALLOWED}


def test_role_check_needs_login_first(client: TestClient) -> None:
    assert client.get("/admin-only").status_code == 401


def test_reviewer_passes_a_check_naming_reviewer(client: TestClient, session: Session) -> None:
    user = _user(session, "Clinic A", "a@example.com", UserRole.REVIEWER)
    _login(client, user_id=user.id, account_id=user.account_id)
    assert client.get("/reviewers").status_code == 200


def test_require_role_refuses_no_roles() -> None:
    with pytest.raises(ValueError, match="at least one role"):
        require_role()


def test_session_dependency_uses_the_factory_on_app_state(engine: Engine) -> None:
    app = FastAPI()
    app.state.session_factory = create_session_factory(engine)

    @app.get("/ping-db")
    def ping_db(db: Annotated[Session, Depends(get_db_session)]) -> int:
        return int(db.execute(text("SELECT 1")).scalar_one())

    with TestClient(app) as test_client:
        response = test_client.get("/ping-db")

    assert response.status_code == 200
    assert response.json() == 1
