"""Password reset for a user who cannot log in: ask for a code by email, then set a new password.

Both routes count every try against the client IP's login limit, by IP only, so no answer
depends on the email. Asking always gives the same 202; the email goes out after the answer.
Confirming gives the same 400 for an unknown email as for a wrong code.
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import get_api_settings, get_mail_sender, get_now, get_session_factory
from src.api.email_codes import (
    CODE_INVALID,
    add_code,
    check_code,
    delete_codes,
    hash_code,
    new_code,
    resend_wait_seconds,
)
from src.api.login import find_user_by_email
from src.api.login_limit import (
    clear_email_failures,
    count_ip_try,
    ip_key,
    login_keys,
    refuse_over_ip_limit,
    too_many_tries,
)
from src.api.mail import MailMessage, MailSender, code_email, send_quietly
from src.api.mail_cap import reserve_mail_send
from src.api.passwords import hash_password
from src.api.schemas import PasswordResetAnswer, PasswordResetConfirm, PasswordResetRequest
from src.api.signup import record_joined
from src.config.settings import ApiSettings
from src.db.models import AuditAction, EmailCodePurpose, User
from src.db.query import for_account
from src.db.session import session_scope
from src.review.audit import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

PURPOSE = EmailCodePurpose.PASSWORD_RESET
# The same answer for a known email, an unknown one and one over its hourly cap.
RESET_ASKED = "if the email belongs to an account, a code is on its way"

Settings = Annotated[ApiSettings, Depends(get_api_settings)]
SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]
Now = Annotated[datetime, Depends(get_now)]


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def _locked_user(session: Session, email: str) -> User | None:
    found = find_user_by_email(session, email)
    if found is None:
        return None
    # Locked, so two requests at the same time cannot both pass the cap or the wrong-try count.
    return session.scalars(
        for_account(User, found.account_id).where(User.id == found.id).with_for_update()
    ).one()


def _reset_message(
    session: Session, email: str, *, settings: ApiSettings, now: datetime
) -> MailMessage | None:
    """Store a new reset code for the user with `email` and return their email, or None when
    the email has no account, already got its hourly number of codes, or today's emails are
    at the daily cap."""
    user = _locked_user(session, email)
    if user is None:
        return None
    ids = {"account_id": user.account_id, "user_id": user.id}
    # No gap between two asks: only the hourly cap, as with the old links.
    if (
        resend_wait_seconds(
            session,
            **ids,
            purpose=PURPOSE,
            now=now,
            gap_seconds=0,
            per_hour=settings.password_reset_per_email_per_hour,
        )
        is not None
    ):
        logger.info("password reset not sent: hourly cap reached for user %d", user.id)
        return None
    if not reserve_mail_send(session, PURPOSE, now=now, daily_limit=settings.mail_daily_limit):
        return None
    code = new_code()
    add_code(
        session,
        **ids,
        purpose=PURPOSE,
        code=code,
        secret=settings.auth_token_secret.get_secret_value(),
        ttl_minutes=settings.email_code_ttl_minutes,
        now=now,
    )
    logger.info("password reset code made: account %d, user %d", user.account_id, user.id)
    return code_email(
        PURPOSE, to=user.email, code=code, ttl_minutes=settings.email_code_ttl_minutes
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
) -> PasswordResetAnswer:
    """Email a reset code if `email` has an account; answer the same 202 either way.

    The email is sent after the answer, so a known email does not take longer. Answers 429
    while the client IP has too many recent failures (the limit does not depend on the email).
    """
    ip_hash = ip_key(_client_host(request), settings.auth_token_secret.get_secret_value())
    # Counted and committed first; the row stays, so asking costs the IP one try.
    row_id = count_ip_try(factory, ip_hash, now=now, settings=settings)
    message: MailMessage | None = None
    with session_scope(factory) as session:
        retry_after = refuse_over_ip_limit(session, ip_hash, row_id, now=now, settings=settings)
        if retry_after is None:
            message = _reset_message(session, body.email, settings=settings, now=now)
    if retry_after is not None:
        logger.info("password reset refused: too many tries")
        raise too_many_tries(retry_after)
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
    """Set the new password if `code` is the newest unexpired reset code of `email`'s user.

    Ends every session of the user, deletes all of the user's codes, marks the email verified
    (the code proved the mailbox; the first time, `record_joined` audits the user joining),
    clears the email's failed logins and audits it. Does not log in. A wrong code or an unknown
    email is the same 400; a wrong code counts one wrong try against the code (it dies after
    `EMAIL_CODE_MAX_WRONG_TRIES`), and every failure one against the client IP's limit; over
    that limit, 429 without checking. Takes tens of milliseconds on purpose (the password hash).
    """
    client_host = _client_host(request)
    secret = settings.auth_token_secret.get_secret_value()
    ip_hash = ip_key(client_host, secret)
    row_id = count_ip_try(factory, ip_hash, now=now, settings=settings)
    # The answers are decided inside the transaction and raised after it, so the wrong try
    # written below is committed.
    with session_scope(factory) as session:
        retry_after = refuse_over_ip_limit(session, ip_hash, row_id, now=now, settings=settings)
        user = None if retry_after is not None else _locked_user(session, body.email)
        if retry_after is None and user is None:
            # The same keyed hash as for a known email, so the answer takes about as long.
            hash_code(PURPOSE, 0, body.code, secret=secret)
        elif user is not None and check_code(
            session,
            account_id=user.account_id,
            user_id=user.id,
            purpose=PURPOSE,
            code=body.code,
            secret=secret,
            now=now,
            max_wrong_tries=settings.email_code_max_wrong_tries,
        ):
            user.password_hash = hash_password(body.new_password)
            user.token_version += 1
            if user.email_verified_at is None:
                user.email_verified_at = now
                record_joined(session, user)
            for purpose in EmailCodePurpose:
                delete_codes(session, account_id=user.account_id, user_id=user.id, purpose=purpose)
            email_hash = login_keys(user.email, client_host, secret).email_hash
            clear_email_failures(session, email_hash, row_id)
            record_audit_event(session, user.account_id, user.id, AuditAction.PASSWORD_RESET, None)
            logger.info("password reset: account %d, user %d", user.account_id, user.id)
            return
    if retry_after is not None:
        logger.info("password reset confirm refused: too many tries")
        raise too_many_tries(retry_after)
    # The IP row was committed before the code check, so it stays.
    logger.info("password reset confirm failed: code not valid")
    raise HTTPException(status.HTTP_400_BAD_REQUEST, CODE_INVALID)
