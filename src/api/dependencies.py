"""FastAPI dependencies: settings, the database session, the logged-in user and role checks.

Every route takes the `account_id` it queries with from `current_user`, never from the path,
query or body. The app factory puts `api_settings` and `session_factory` on `app.state`.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from src.api.tokens import InvalidTokenError, read_token
from src.config.settings import ApiSettings
from src.db.models import User, UserRole
from src.db.query import for_account
from src.db.session import session_scope

AUTH_COOKIE_NAME = "access_token"

# One message for every login failure, so the answer does not say which check failed.
NOT_LOGGED_IN = "not logged in"
NOT_ALLOWED = "not allowed for this role"


class CurrentUser(BaseModel):
    """The logged-in user, read from the database on every request."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    account_id: int
    email: str
    role: UserRole


def get_api_settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.api_settings
    return settings


def get_db_session(request: Request) -> Iterator[Session]:
    """Yield a session that commits when the route succeeds and rolls back on any error."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with session_scope(factory) as session:
        yield session


def current_user(
    request: Request,
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    session: Annotated[Session, Depends(get_db_session)],
) -> CurrentUser:
    """Return the user named by the login cookie, or answer 401.

    The user is looked up inside the token's account, so a token whose user and account do not
    belong together is refused like a missing one.
    """
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN)
    try:
        claims = read_token(token, secret=settings.auth_token_secret.get_secret_value())
    except InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN) from None
    user = session.scalars(
        for_account(User, claims.account_id).where(User.id == claims.user_id)
    ).one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN)
    return CurrentUser(
        user_id=user.id, account_id=user.account_id, email=user.email, role=user.role
    )


def require_role(*roles: UserRole) -> Callable[[CurrentUser], CurrentUser]:
    """Build a dependency that answers 403 unless the logged-in user has one of `roles`."""
    if not roles:
        raise ValueError("require_role needs at least one role")
    allowed = frozenset(roles)

    def check_role(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_ALLOWED)
        return user

    return check_role
