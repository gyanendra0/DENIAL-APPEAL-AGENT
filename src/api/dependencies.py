"""FastAPI dependencies: settings, the database session, the job runner, the mail sender,
Google's side of the sign-in, the clock, the logged-in user and role checks.

Every route takes the `account_id` it queries with from `current_user`, never from the path,
query or body. The app factory puts `api_settings`, `session_factory`, `job_runner`,
`mail_sender` and `google` (None when not configured) on `app.state`.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from src.api.google_oidc import GoogleOidc
from src.api.mail import MailSender
from src.api.tokens import InvalidTokenError, read_token
from src.config.settings import ApiSettings
from src.db.models import User, UserRole
from src.db.query import for_account
from src.db.session import session_scope

AUTH_COOKIE_NAME = "access_token"

# One message for every login failure, so the answer does not say which check failed.
NOT_LOGGED_IN = "not logged in"
NOT_ALLOWED = "not allowed for this role"

# Runs one queued upload job, given its account id and job id. Called in the background,
# after the upload was answered.
JobRunner = Callable[[int, int], None]


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


def get_job_runner(request: Request) -> JobRunner:
    runner: JobRunner = request.app.state.job_runner
    return runner


def get_mail_sender(request: Request) -> MailSender:
    sender: MailSender = request.app.state.mail_sender
    return sender


def get_google(request: Request) -> GoogleOidc:
    """Google's side of "Continue with Google", or 404 when it is not configured."""
    google: GoogleOidc | None = request.app.state.google
    if google is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return google


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """The app's session factory, for a route that needs more than one transaction."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    return factory


def get_now() -> datetime:
    """The current time in UTC. Tests override it, so a time window needs no waiting."""
    return datetime.now(UTC)


def get_db_session(request: Request) -> Iterator[Session]:
    """Yield a session that commits when the route succeeds and rolls back on any error.

    Use it through `DbSession`, so the commit runs before the answer is sent.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    with session_scope(factory) as session:
        yield session


# `scope="function"` closes the session (and commits) when the route returns, before the answer
# is sent. The default closes it after the answer, so a client could hear 201 for a write that
# then fails to commit.
DbSession = Annotated[Session, Depends(get_db_session, scope="function")]


def _user_from_cookie(request: Request, settings: ApiSettings, session: Session) -> CurrentUser:
    """Return the user named by the login cookie, or answer 401.

    The user is looked up inside the token's account, so a token whose user and account do not
    belong together is refused like a missing one. A token issued before the user's last
    password change carries an older version and is refused the same way.
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
    if user is None or user.token_version != claims.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN)
    return CurrentUser(
        user_id=user.id, account_id=user.account_id, email=user.email, role=user.role
    )


def current_user(
    request: Request,
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    session: DbSession,
) -> CurrentUser:
    """Return the logged-in user (see `_user_from_cookie`), read in the route's own session."""
    return _user_from_cookie(request, settings, session)


def current_user_short(
    request: Request,
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> CurrentUser:
    """Return the logged-in user like `current_user`, read in a transaction that ends before
    the route runs.

    For a route that opens its own transactions (the login limit commits its count in one and
    must keep it when a later one fails): the route then holds no other session or connection.
    """
    with session_scope(factory) as session:
        return _user_from_cookie(request, settings, session)


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
