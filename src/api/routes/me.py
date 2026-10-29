"""The logged-in user's own record, changing their own password, and deleting their own
personal account."""

import logging
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
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
from src.api.email_codes import delete_codes
from src.api.login_cookie import clear_login_cookie, set_login_cookie
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
from src.api.schemas import (
    MAX_LOGIN_PASSWORD_CHARACTERS,
    AccountDeleteRequest,
    PasswordChangeRequest,
    UserResponse,
)
from src.api.user_lookup import find_user_response
from src.config.settings import ApiSettings
from src.db.models import (
    Account,
    AccountKind,
    AuditAction,
    EmailCodePurpose,
    Job,
    JobStatus,
    User,
)
from src.db.query import for_account
from src.db.session import session_scope
from src.review.audit import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["users"])

# A fixed sentence: the answer never repeats what was typed.
WRONG_CURRENT_PASSWORD = "the current password is wrong"  # noqa: S105  (a message, not a password)
NO_PASSWORD_YET = (
    "this login has no password yet; use Forgot password to set one"  # noqa: S105  (a message)
)
WRONG_PASSWORD = "the password is wrong"  # noqa: S105  (a message, not a password)
ONLY_PERSONAL_ACCOUNTS = "only a personal account can be deleted here"
LOGIN_AGAIN = "log in again, then delete your account within 10 minutes"
LETTER_STILL_RUNNING = "a letter is still being read; wait until it is finished, then delete"

# A user with no password proves it is them by having logged in this recently.
RECENT_LOGIN_MINUTES = 10

LoggedIn = Annotated[CurrentUser, Depends(current_user)]
Settings = Annotated[ApiSettings, Depends(get_api_settings)]
SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]
Now = Annotated[datetime, Depends(get_now)]


@router.get("/me")
def me(user: LoggedIn, settings: Settings, session: DbSession) -> UserResponse:
    """Return the logged-in user and their account."""
    found = find_user_response(
        session, settings=settings, account_id=user.account_id, user_id=user.user_id
    )
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
    the limit, 429 without checking. A user with no password yet gets a 400 that says so,
    without counting. The answer carries a new login cookie, so this session stays
    logged in while every older token stops working; every reset code of the user stops working
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
    no_password = False
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
            elif row.password_hash is None:
                # Nothing was guessed, so the try does not count.
                no_password = True
                release_failure(session, ids)
            elif len(body.current_password) <= MAX_LOGIN_PASSWORD_CHARACTERS and verify_password(
                row.password_hash, body.current_password
            ):
                row.password_hash = hash_password(body.new_password)
                row.token_version += 1
                # A reset code asked for before the change must not undo it.
                delete_codes(
                    session,
                    account_id=row.account_id,
                    user_id=row.id,
                    purpose=EmailCodePurpose.PASSWORD_RESET,
                )
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
    if no_password:
        logger.info("password change refused: no password set yet")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, NO_PASSWORD_YET)
    # The failure rows were committed before the password check, so they stay.
    logger.info("password change failed: wrong current password")
    raise HTTPException(status.HTTP_400_BAD_REQUEST, WRONG_CURRENT_PASSWORD)


@router.post("/me/delete-account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    body: AccountDeleteRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user_short)],
    settings: Settings,
    factory: SessionFactory,
    now: Now,
) -> None:
    """Delete the logged-in individual's personal account and everything in it, and log out.

    Only a personal account (one user, their own letters) can be deleted; a user of the shared
    account gets a 403, since their uploads belong to the team's work. A user with a password
    must send it: a wrong or missing one is a 400 that counts toward the login limit like a
    wrong current password at `/me/password` (over the limit, 429 without checking). A user
    who logs in only with Google has no password and must have logged in within the last
    `RECENT_LOGIN_MINUTES`, else 403. While one of the account's letters is still queued or
    being read, 409: the job would go on spending on an account that is gone.

    One `DELETE` of the `accounts` row removes every business row of the account (uploads,
    jobs, drafts, revisions, audit events, codes, Google link) through `ON DELETE CASCADE`, in
    the same transaction. `llm_calls` and the daily counter stay (no account, no content). The
    deletion is logged with the account id only. Takes tens of milliseconds on purpose (one
    password hash).
    """
    if user.account_kind is not AccountKind.PERSONAL:
        logger.info("account delete refused: account %d is not personal", user.account_id)
        raise HTTPException(status.HTTP_403_FORBIDDEN, ONLY_PERSONAL_ACCOUNTS)
    client_host = request.client.host if request.client is not None else None
    keys = login_keys(user.email, client_host, settings.auth_token_secret.get_secret_value())
    # The same two steps as `change_password`: count the try first and commit, then decide
    # inside a second transaction and raise only after it.
    with session_scope(factory) as session:
        ids = reserve_failure(
            session, keys, now=now, window_minutes=settings.login_limit_window_minutes
        )
    retry_after: int | None = None
    gone = False
    login_again = False
    still_running = False
    with session_scope(factory) as session:
        if over_limit(session, keys, now=now, settings=settings):
            release_failure(session, ids)
            session.flush()
            retry_after = retry_after_seconds(session, keys, now=now, settings=settings) or 1
        else:
            # The upload cap locks the same row, so no upload is added while this runs.
            account = session.scalars(
                select(Account.id).where(Account.id == user.account_id).with_for_update()
            ).one_or_none()
            row = session.scalars(
                for_account(User, user.account_id).where(User.id == user.user_id)
            ).one_or_none()
            confirmed = False
            if account is None or row is None:
                gone = True
            elif row.password_hash is None:
                # Nothing was guessed, so the try does not count.
                release_failure(session, ids)
                login_again = now - user.logged_in_at > timedelta(minutes=RECENT_LOGIN_MINUTES)
                confirmed = not login_again
            elif (
                body.password is not None
                and len(body.password) <= MAX_LOGIN_PASSWORD_CHARACTERS
                and verify_password(row.password_hash, body.password)
            ):
                clear_email_failures(session, keys.email_hash, ids.ip_id)
                confirmed = True
            if confirmed:
                still_running = _has_unfinished_job(session, user.account_id)
            if confirmed and not still_running:
                session.expunge_all()
                session.execute(delete(Account).where(Account.id == user.account_id))
                clear_login_cookie(response, settings)
                logger.info("account deleted: account %d", user.account_id)
                return
    if retry_after is not None:
        logger.info("account delete refused: too many failures")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TOO_MANY_LOGINS,
            headers={"Retry-After": str(retry_after)},
        )
    if gone:
        # Deleted between the login check and this read.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN)
    if login_again:
        logger.info("account delete refused: login too old")
        raise HTTPException(status.HTTP_403_FORBIDDEN, LOGIN_AGAIN)
    if still_running:
        logger.info("account delete refused: a job is unfinished")
        raise HTTPException(status.HTTP_409_CONFLICT, LETTER_STILL_RUNNING)
    # The failure rows were committed before the password check, so they stay.
    logger.info("account delete failed: wrong password")
    raise HTTPException(status.HTTP_400_BAD_REQUEST, WRONG_PASSWORD)


def _has_unfinished_job(session: Session, account_id: int) -> bool:
    unfinished = session.scalars(
        for_account(Job, account_id)
        .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        .limit(1)
    )
    return unfinished.first() is not None
