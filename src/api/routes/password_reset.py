"""Password reset for a user who cannot log in: ask for a link by email, then set a new password.

Both routes count every try against the client IP's login limit, by IP only, so no answer
depends on the email. Asking always gives the same 202; the email goes out after the answer.
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import (
    get_api_settings,
    get_mail_sender,
    get_now,
    get_public_url,
    get_session_factory,
)
from src.api.login import find_user_by_email
from src.api.login_limit import (
    TOO_MANY_LOGINS,
    clear_email_failures,
    ip_key,
    ip_over_limit,
    ip_retry_after_seconds,
    login_keys,
    release_ip_failure,
    reserve_ip_failure,
)
from src.api.mail import MailMessage, MailSender, reset_email, send_quietly
from src.api.passwords import hash_password
from src.api.reset_tokens import (
    InvalidResetTokenError,
    add_reset_token,
    count_recent_reset_tokens,
    delete_reset_tokens,
    delete_stale_reset_tokens,
    issue_reset_token,
    read_reset_token,
    usable_reset_token,
)
from src.api.schemas import PasswordResetAnswer, PasswordResetConfirm, PasswordResetRequest
from src.config.settings import ApiSettings
from src.db.models import AuditAction, User
from src.db.query import for_account
from src.db.session import session_scope
from src.review.audit import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# The same answer for a known email, an unknown one and one over its hourly cap.
RESET_ASKED = "if the email belongs to an account, a link is on its way"
# One sentence for every token problem: damaged, expired, used or replaced.
RESET_LINK_INVALID = "the reset link is not valid or has expired"
# The screen that reads the token. It travels in the fragment, which browsers never send to a
# server, so it stays out of server logs and `Referer` headers.
RESET_PATH = "/app/reset-password#token="

Settings = Annotated[ApiSettings, Depends(get_api_settings)]
SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]
Now = Annotated[datetime, Depends(get_now)]


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        TOO_MANY_LOGINS,
        headers={"Retry-After": str(retry_after)},
    )


def _reset_message(
    session: Session, email: str, *, settings: ApiSettings, now: datetime, public_url: str
) -> MailMessage | None:
    """Store a new token for the user with `email` and return their email, or None when the
    email has no account or already got its hourly number of links."""
    found = find_user_by_email(session, email)
    if found is None:
        return None
    # Locked, so two requests at the same time cannot both pass the cap.
    user = session.scalars(
        for_account(User, found.account_id).where(User.id == found.id).with_for_update()
    ).one()
    ids = {"account_id": user.account_id, "user_id": user.id}
    delete_stale_reset_tokens(session, **ids, now=now)
    if count_recent_reset_tokens(session, **ids, now=now) >= (
        settings.password_reset_per_email_per_hour
    ):
        logger.info("password reset not sent: hourly cap reached for user %d", user.id)
        return None
    issued = issue_reset_token(
        **ids,
        secret=settings.auth_token_secret.get_secret_value(),
        ttl_minutes=settings.password_reset_ttl_minutes,
        now=now,
    )
    add_reset_token(session, **ids, issued=issued, now=now)
    logger.info("password reset link made: account %d, user %d", user.account_id, user.id)
    return reset_email(
        to=user.email,
        link=public_url + RESET_PATH + issued.token,
        ttl_minutes=settings.password_reset_ttl_minutes,
    )


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED)
def ask_password_reset(
    body: PasswordResetRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings,
    factory: SessionFactory,
    now: Now,
    sender: Annotated[MailSender, Depends(get_mail_sender)],
    public_url: Annotated[str, Depends(get_public_url)],
) -> PasswordResetAnswer:
    """Email a reset link if `email` has an account; answer the same 202 either way.

    The email is sent after the answer, so a known email does not take longer. Answers 429
    while the client IP has too many recent failures (the limit does not depend on the email).
    """
    ip_hash = ip_key(_client_host(request), settings.auth_token_secret.get_secret_value())
    # Counted first and committed, as at login (see `routes/auth.py`); the row stays, so asking
    # costs the IP one try.
    with session_scope(factory) as session:
        row_id = reserve_ip_failure(
            session, ip_hash, now=now, window_minutes=settings.login_limit_window_minutes
        )
    retry_after: int | None = None
    message: MailMessage | None = None
    with session_scope(factory) as session:
        if ip_over_limit(session, ip_hash, now=now, settings=settings):
            release_ip_failure(session, row_id)
            session.flush()
            retry_after = ip_retry_after_seconds(session, ip_hash, now=now, settings=settings) or 1
        else:
            message = _reset_message(
                session, body.email, settings=settings, now=now, public_url=public_url
            )
    if retry_after is not None:
        logger.info("password reset refused: too many tries")
        raise _too_many(retry_after)
    if message is not None:
        background_tasks.add_task(send_quietly, sender, message)
    logger.info("password reset asked")
    return PasswordResetAnswer(detail=RESET_ASKED)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(
    body: PasswordResetConfirm,
    request: Request,
    settings: Settings,
    factory: SessionFactory,
    now: Now,
) -> None:
    """Set the new password if the token is the user's newest one and has not expired.

    Ends every session of the user, makes every reset link of the user useless, clears the
    email's failed logins and audits it. Does not log in. A bad token is a 400 and counts
    toward the client IP's limit; over the limit, 429 without checking. Takes tens of
    milliseconds on purpose (the password hash).
    """
    client_host = _client_host(request)
    secret = settings.auth_token_secret.get_secret_value()
    ip_hash = ip_key(client_host, secret)
    with session_scope(factory) as session:
        row_id = reserve_ip_failure(
            session, ip_hash, now=now, window_minutes=settings.login_limit_window_minutes
        )
    retry_after: int | None = None
    with session_scope(factory) as session:
        if ip_over_limit(session, ip_hash, now=now, settings=settings):
            release_ip_failure(session, row_id)
            session.flush()
            retry_after = ip_retry_after_seconds(session, ip_hash, now=now, settings=settings) or 1
        else:
            try:
                claims = read_reset_token(body.token, secret=secret, now=now)
            except InvalidResetTokenError:
                claims = None
            # The account comes from the signed token, so this query has its `account_id`.
            user = (
                None
                if claims is None
                else session.scalars(
                    for_account(User, claims.account_id)
                    .where(User.id == claims.user_id)
                    .with_for_update()
                ).one_or_none()
            )
            if (
                claims is not None
                and user is not None
                and usable_reset_token(session, claims, now=now)
            ):
                user.password_hash = hash_password(body.new_password)
                user.token_version += 1
                delete_reset_tokens(session, account_id=user.account_id, user_id=user.id)
                email_hash = login_keys(user.email, client_host, secret).email_hash
                clear_email_failures(session, email_hash, row_id)
                record_audit_event(
                    session, user.account_id, user.id, AuditAction.PASSWORD_RESET, None
                )
                logger.info("password reset: account %d, user %d", user.account_id, user.id)
                return
    if retry_after is not None:
        logger.info("password reset confirm refused: too many tries")
        raise _too_many(retry_after)
    # The IP row was committed before the token check, so it stays.
    logger.info("password reset confirm failed: link not valid")
    raise HTTPException(status.HTTP_400_BAD_REQUEST, RESET_LINK_INVALID)
