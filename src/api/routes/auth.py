"""Sign-up, login and logout. The login token travels only in an HttpOnly cookie."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from src.api.dependencies import AUTH_COOKIE_NAME, get_api_settings, get_db_session
from src.api.login import authenticate
from src.api.schemas import LoginRequest, SignupRequest, UserResponse
from src.api.signup import AccountNameTakenError, EmailTakenError, create_account_with_admin
from src.api.tokens import issue_token
from src.api.user_lookup import find_user_response
from src.config.settings import ApiSettings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# One message for an unknown email and a wrong password, so the answer does not say which.
LOGIN_FAILED = "email or password is wrong"
ACCOUNT_NAME_TAKEN = "the account name is taken"
EMAIL_TAKEN = "the email is taken"

Settings = Annotated[ApiSettings, Depends(get_api_settings)]
DbSession = Annotated[Session, Depends(get_db_session)]


def _set_login_cookie(
    response: Response, settings: ApiSettings, *, user_id: int, account_id: int
) -> None:
    token = issue_token(
        user_id=user_id,
        account_id=account_id,
        secret=settings.auth_token_secret.get_secret_value(),
        ttl_minutes=settings.auth_token_ttl_minutes,
        now=datetime.now(UTC),
    )
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=settings.auth_token_ttl_minutes * 60,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )


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
    """Create a new account and its first user (an admin), and log that user in."""
    try:
        user = create_account_with_admin(
            session, account_name=body.account_name, email=body.email, password=body.password
        )
    except AccountNameTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, ACCOUNT_NAME_TAKEN) from None
    except EmailTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, EMAIL_TAKEN) from None
    _set_login_cookie(response, settings, user_id=user.id, account_id=user.account_id)
    logger.info("signup: account %d, user %d", user.account_id, user.id)
    return _user_response(session, account_id=user.account_id, user_id=user.id)


@router.post("/login")
def login(
    body: LoginRequest, response: Response, settings: Settings, session: DbSession
) -> UserResponse:
    """Check email and password and set the login cookie, or answer 401."""
    user = authenticate(session, email=body.email, password=body.password)
    if user is None:
        logger.info("login failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGIN_FAILED)
    _set_login_cookie(response, settings, user_id=user.id, account_id=user.account_id)
    logger.info("login: account %d, user %d", user.account_id, user.id)
    return _user_response(session, account_id=user.account_id, user_id=user.id)


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
