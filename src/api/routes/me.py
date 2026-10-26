"""The logged-in user's own record, and changing their own password."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import (
    NOT_LOGGED_IN,
    CurrentUser,
    DbSession,
    current_user,
    current_user_short,
    get_api_settings,
    get_now,
    get_session_factory,
)
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
from src.api.passwords import hash_password, verify_password
from src.api.reset_tokens import delete_reset_tokens
from src.api.schemas import MAX_LOGIN_PASSWORD_CHARACTERS, PasswordChangeRequest, UserResponse
from src.api.user_lookup import find_user_response
from src.config.settings import ApiSettings
from src.db.models import AuditAction, User
from src.db.query import for_account
from src.db.session import session_scope
from src.review.audit import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["users"])

# A fixed sentence: the answer never repeats what was typed.
WRONG_CURRENT_PASSWORD = "the current password is wrong"  # noqa: S105  (a message, not a password)

LoggedIn = Annotated[CurrentUser, Depends(current_user)]
Settings = Annotated[ApiSettings, Depends(get_api_settings)]
SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]
Now = Annotated[datetime, Depends(get_now)]


@router.get("/me")
def me(user: LoggedIn, session: DbSession) -> UserResponse:
    """Return the logged-in user and their account."""
    found = find_user_response(session, account_id=user.account_id, user_id=user.user_id)
    if found is None:
        # Deleted between the login check and this read.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN)
    return found


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user_short)],
    settings: Settings,
    factory: SessionFactory,
    now: Now,
) -> None:
    """Check the current password, store the new one, end the user's other sessions and audit it.

    A wrong current password is a 400 and counts toward the login limit of the user's email and
    the client IP, so a stolen session cannot guess the password faster than login could; over
    the limit, 429 without checking. The answer carries a new login cookie, so this session stays
    logged in while every older token stops working; every reset link of the user stops working
    too. Takes tens of milliseconds on purpose (two password hashes).
    """
    client_host = request.client.host if request.client is not None else None
    keys = login_keys(user.email, client_host, settings.auth_token_secret.get_secret_value())
    # The same two steps as login (see `routes/auth.py`): count the try first and commit, then
    # decide inside a second transaction and raise only after it.
    with session_scope(factory) as session:
        ids = reserve_failure(
            session, keys, now=now, window_minutes=settings.login_limit_window_minutes
        )
    retry_after: int | None = None
    gone = False
    with session_scope(factory) as session:
        if over_limit(session, keys, now=now, settings=settings):
            release_failure(session, ids)
            session.flush()
            retry_after = retry_after_seconds(session, keys, now=now, settings=settings) or 1
        else:
            # Locked, so two changes at the same time cannot both raise the version to one value.
            row = session.scalars(
                for_account(User, user.account_id).where(User.id == user.user_id).with_for_update()
            ).one_or_none()
            if row is None:
                gone = True
            elif len(body.current_password) <= MAX_LOGIN_PASSWORD_CHARACTERS and verify_password(
                row.password_hash, body.current_password
            ):
                row.password_hash = hash_password(body.new_password)
                row.token_version += 1
                # A reset link asked for before the change must not undo it.
                delete_reset_tokens(session, account_id=row.account_id, user_id=row.id)
                clear_email_failures(session, keys.email_hash, ids.ip_id)
                record_audit_event(
                    session, row.account_id, row.id, AuditAction.PASSWORD_CHANGED, None
                )
                set_login_cookie(
                    response,
                    settings,
                    user_id=row.id,
                    account_id=row.account_id,
                    token_version=row.token_version,
                )
                logger.info("password changed: account %d, user %d", row.account_id, row.id)
                return
    if retry_after is not None:
        logger.info("password change refused: too many failures")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TOO_MANY_LOGINS,
            headers={"Retry-After": str(retry_after)},
        )
    if gone:
        # Deleted between the login check and this read.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN)
    # The failure rows were committed before the password check, so they stay.
    logger.info("password change failed: wrong current password")
    raise HTTPException(status.HTTP_400_BAD_REQUEST, WRONG_CURRENT_PASSWORD)
