"""Sign-up, login and logout. The login token travels only in an HttpOnly cookie."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import (
    AUTH_COOKIE_NAME,
    DbSession,
    get_api_settings,
    get_now,
    get_session_factory,
)
from src.api.login import authenticate
from src.api.login_cookie import set_login_cookie
from src.api.login_limit import (
    TOO_MANY_LOGINS,
    clear_email_failures,
    login_keys,
    over_limit,
    release_failure,
    reserve_failure,
    retry_after_seconds,
)
from src.api.schemas import MAX_LOGIN_PASSWORD_CHARACTERS, LoginRequest, SignupRequest, UserResponse
from src.api.signup import AccountNameTakenError, EmailTakenError, create_account_with_admin
from src.api.user_lookup import find_user_response
from src.config.settings import ApiSettings
from src.db.models import AuditAction
from src.db.session import session_scope
from src.review.audit import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# One message for an unknown email and a wrong password, so the answer does not say which.
LOGIN_FAILED = "email or password is wrong"
ACCOUNT_NAME_TAKEN = "the account name is taken"
EMAIL_TAKEN = "the email is taken"

Settings = Annotated[ApiSettings, Depends(get_api_settings)]
SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]
Now = Annotated[datetime, Depends(get_now)]


def _user_response(session: Session, *, account_id: int, user_id: int) -> UserResponse:
    found = find_user_response(session, account_id=account_id, user_id=user_id)
    if found is None:
        # The user was written or read in this same transaction; missing now is a bug.
        raise RuntimeError(f"user {user_id} of account {account_id} vanished")
    return found


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(
    body: SignupRequest, response: Response, settings: Settings, session: DbSession
) -> UserResponse:
    """Create a new account and its first user (an admin), audit it, and log that user in."""
    try:
        user = create_account_with_admin(
            session, account_name=body.account_name, email=body.email, password=body.password
        )
    except AccountNameTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_NAME_TAKEN) from None
    except EmailTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, EMAIL_TAKEN) from None
    record_audit_event(session, user.account_id, user.id, AuditAction.ACCOUNT_CREATED, None)
    set_login_cookie(
        response,
        settings,
        user_id=user.id,
        account_id=user.account_id,
        token_version=user.token_version,
    )
    logger.info("signup: account %d, user %d", user.account_id, user.id)
    return _user_response(session, account_id=user.account_id, user_id=user.id)


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings,
    factory: SessionFactory,
    now: Now,
) -> UserResponse:
    """Check email and password and set the login cookie, or answer 401.

    Answers 429 without checking the password while the email or the client IP has too many
    recent failures. Takes tens of milliseconds on purpose (the password hash).
    """
    client_host = request.client.host if request.client is not None else None
    keys = login_keys(body.email, client_host, settings.auth_token_secret.get_secret_value())
    # The try is counted as a failure first and committed, then the count is read: tries running
    # at the same time see each other's rows, so at most the limit reach the password check.
    with session_scope(factory) as session:
        ids = reserve_failure(
            session, keys, now=now, window_minutes=settings.login_limit_window_minutes
        )
    # Every answer is decided inside the transaction and raised after it: an error rolls back the
    # transaction it is raised in, which would undo the deletes below.
    retry_after: int | None = None
    with session_scope(factory) as session:
        if over_limit(session, keys, now=now, settings=settings):
            release_failure(session, ids)
            session.flush()
            # Without its own rows the count is at the limit again, so there is a wait; a good
            # login of another try may have cleared the rows meanwhile, hence the floor.
            retry_after = retry_after_seconds(session, keys, now=now, settings=settings) or 1
        else:
            # A password longer than any sign-up allows can never match; refusing it here keeps a
            # huge body from being hashed. The length does not depend on the email, so this says
            # nothing.
            too_long = len(body.password) > MAX_LOGIN_PASSWORD_CHARACTERS
            user = (
                None
                if too_long
                else authenticate(session, email=body.email, password=body.password)
            )
            if user is not None:
                clear_email_failures(session, keys, ids)
                set_login_cookie(
                    response,
                    settings,
                    user_id=user.id,
                    account_id=user.account_id,
                    token_version=user.token_version,
                )
                logger.info("login: account %d, user %d", user.account_id, user.id)
                return _user_response(session, account_id=user.account_id, user_id=user.id)
    if retry_after is not None:
        logger.info("login refused: too many failures")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TOO_MANY_LOGINS,
            headers={"Retry-After": str(retry_after)},
        )
    # The failure rows were committed before the password check, so they stay.
    logger.info("login failed")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGIN_FAILED)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, settings: Settings) -> None:
    """Clear the login cookie. The token itself stays valid until it expires."""
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )
