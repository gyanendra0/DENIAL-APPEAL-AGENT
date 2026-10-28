"""Confirming the email of a new sign-up: type the emailed code, or ask for a new one.

Nobody is logged in yet, so the user comes from the signed `email_verify` cookie (set by sign-up
and by a login whose email is not confirmed), never from the body. Both routes count every try
against the client IP's limit, like login.
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import get_api_settings, get_mail_sender, get_now, get_session_factory
from src.api.email_codes import CODE_INVALID, check_code, delete_codes
from src.api.email_verification import (
    MAIL_UNAVAILABLE,
    PURPOSE,
    CodeTooSoonError,
    MailCapReachedError,
    prepare_verify_email,
)
from src.api.login_cookie import set_login_cookie
from src.api.login_limit import (
    count_ip_try,
    ip_key,
    refuse_over_ip_limit,
    release_ip_failure,
    too_many_tries,
)
from src.api.mail import MailMessage, MailSender, send_quietly
from src.api.schemas import CodeSentAnswer, UserResponse, VerifyEmailRequest
from src.api.signup import record_joined
from src.api.user_lookup import find_user_response
from src.api.verify_cookie import (
    VERIFY_COOKIE_NAME,
    InvalidVerifyCookieError,
    VerifyClaims,
    delete_verify_cookie,
    read_verify_cookie,
)
from src.config.settings import ApiSettings
from src.db.models import AuditAction, User
from src.db.query import for_account
from src.db.session import session_scope
from src.review.audit import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

VERIFY_EXPIRED = "the code screen has expired; log in again to get a new code"
ALREADY_VERIFIED = "the email is already confirmed; log in"
CODE_SENT = "a new code is on its way"
CODE_TOO_SOON = "a code was sent a moment ago; wait before asking for another"

Settings = Annotated[ApiSettings, Depends(get_api_settings)]
SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]
Now = Annotated[datetime, Depends(get_now)]


def _claims(request: Request, settings: ApiSettings, now: datetime) -> VerifyClaims:
    value = request.cookies.get(VERIFY_COOKIE_NAME)
    if value is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, VERIFY_EXPIRED)
    try:
        return read_verify_cookie(
            value, secret=settings.auth_token_secret.get_secret_value(), now=now
        )
    except InvalidVerifyCookieError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, VERIFY_EXPIRED) from None


def _ip_hash(request: Request, settings: ApiSettings) -> str:
    client_host = request.client.host if request.client is not None else None
    return ip_key(client_host, settings.auth_token_secret.get_secret_value())


def _locked_user(session: Session, claims: VerifyClaims) -> User | None:
    # The account comes from the signed cookie, so this query has its `account_id`.
    return session.scalars(
        for_account(User, claims.account_id).where(User.id == claims.user_id).with_for_update()
    ).one_or_none()


@router.post("/verify-email")
def verify_email(
    body: VerifyEmailRequest,
    request: Request,
    response: Response,
    settings: Settings,
    factory: SessionFactory,
    now: Now,
) -> UserResponse:
    """Confirm the email if `code` is the user's newest unexpired sign-up code, and log in.

    Marks the email verified, deletes the user's sign-up codes, audits it (and that the user
    created or joined the account, `record_joined`), sets the login cookie and clears the
    `email_verify` cookie. A wrong code is a 400 and counts one wrong try against the code (it
    dies after `EMAIL_CODE_MAX_WRONG_TRIES`) and one against the client IP's limit; over that
    limit, 429 without checking. No usable cookie: 401.
    """
    claims = _claims(request, settings, now)
    ip_hash = _ip_hash(request, settings)
    row_id = count_ip_try(factory, ip_hash, now=now, settings=settings)
    # The answers are decided inside the transaction and raised after it, so the wrong try
    # written below is committed.
    gone = False
    with session_scope(factory) as session:
        retry_after = refuse_over_ip_limit(session, ip_hash, row_id, now=now, settings=settings)
        user = None if retry_after is not None else _locked_user(session, claims)
        if retry_after is None and user is None:
            gone = True
        elif user is not None and check_code(
            session,
            account_id=user.account_id,
            user_id=user.id,
            purpose=PURPOSE,
            code=body.code,
            secret=settings.auth_token_secret.get_secret_value(),
            now=now,
            max_wrong_tries=settings.email_code_max_wrong_tries,
        ):
            user.email_verified_at = now
            delete_codes(session, account_id=user.account_id, user_id=user.id, purpose=PURPOSE)
            # A good try does not count, as at login.
            release_ip_failure(session, row_id)
            record_joined(session, user)
            record_audit_event(session, user.account_id, user.id, AuditAction.EMAIL_VERIFIED, None)
            set_login_cookie(
                response,
                settings,
                user_id=user.id,
                account_id=user.account_id,
                token_version=user.token_version,
            )
            delete_verify_cookie(response, settings)
            logger.info("email verified: account %d, user %d", user.account_id, user.id)
            found = find_user_response(session, account_id=user.account_id, user_id=user.id)
            if found is None:
                # The user was locked in this same transaction; missing now is a bug.
                raise RuntimeError(f"user {user.id} of account {user.account_id} vanished")
            return found
    if retry_after is not None:
        logger.info("email verify refused: too many tries")
        raise too_many_tries(retry_after)
    if gone:
        # The user was replaced by a new sign-up for the same email.
        logger.info("email verify refused: user no longer exists")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, VERIFY_EXPIRED)
    logger.info("email verify failed: code not valid")
    raise HTTPException(status.HTTP_400_BAD_REQUEST, CODE_INVALID)


@router.post("/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
def resend_code(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings,
    factory: SessionFactory,
    now: Now,
    sender: Annotated[MailSender, Depends(get_mail_sender)],
) -> CodeSentAnswer:
    """Email the user a new sign-up code, which makes the older one useless.

    Answers 429 + `Retry-After` within `EMAIL_CODE_RESEND_SECONDS` of the last code, after
    `EMAIL_CODE_PER_USER_PER_HOUR` codes in an hour, or while the client IP is over its limit
    (every try counts); 503 over the daily email cap; 409 if the email is already confirmed; 401
    without a usable cookie. The email is sent after the answer.
    """
    claims = _claims(request, settings, now)
    ip_hash = _ip_hash(request, settings)
    row_id = count_ip_try(factory, ip_hash, now=now, settings=settings)
    # Every refusal writes nothing beyond the IP row, which was committed before, so it may be
    # raised inside the transaction.
    message: MailMessage | None = None
    with session_scope(factory) as session:
        retry_after = refuse_over_ip_limit(session, ip_hash, row_id, now=now, settings=settings)
        if retry_after is None:
            user = _locked_user(session, claims)
            if user is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, VERIFY_EXPIRED)
            if user.email_verified_at is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_VERIFIED)
            try:
                message = prepare_verify_email(
                    session,
                    account_id=user.account_id,
                    user_id=user.id,
                    settings=settings,
                    now=now,
                )
            except CodeTooSoonError as too_soon:
                logger.info("code resend refused: too soon")
                raise too_many_tries(too_soon.retry_after, CODE_TOO_SOON) from None
            except MailCapReachedError:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, MAIL_UNAVAILABLE) from None
    if message is None:
        logger.info("code resend refused: too many tries")
        raise too_many_tries(retry_after or 1)
    background_tasks.add_task(send_quietly, sender, message)
    logger.info("code resent")
    return CodeSentAnswer(detail=CODE_SENT)
