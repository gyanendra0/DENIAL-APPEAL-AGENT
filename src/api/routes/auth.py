"""Sign-up, login and logout. The login token travels only in an HttpOnly cookie.

Sign-up does not log in: it emails a 6-digit code, and the user is logged in once the code is
typed (`routes/verify_email.py`). Login refuses a user whose email is not confirmed yet.
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import (
    get_api_settings,
    get_mail_sender,
    get_now,
    get_session_factory,
)
from src.api.email_verification import (
    MAIL_UNAVAILABLE,
    CodeTooSoonError,
    MailCapReachedError,
    prepare_verify_email,
)
from src.api.login import authenticate
from src.api.login_cookie import clear_login_cookie, set_login_cookie
from src.api.login_limit import (
    clear_email_failures,
    count_ip_try,
    ip_key,
    login_keys,
    over_limit,
    refuse_over_ip_limit,
    release_failure,
    reserve_failure,
    retry_after_seconds,
    too_many_tries,
)
from src.api.mail import MailMessage, MailSender, send_quietly
from src.api.schemas import (
    MAX_LOGIN_PASSWORD_CHARACTERS,
    EmailNotVerifiedAnswer,
    LoginRequest,
    SignupRequest,
    UserResponse,
)
from src.api.signup import EmailTakenError, create_member, signup_kind
from src.api.user_lookup import find_user_response
from src.api.verify_cookie import set_verify_cookie
from src.config.settings import ApiSettings
from src.db.session import session_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# One message for an unknown email and a wrong password, so the answer does not say which.
LOGIN_FAILED = "email or password is wrong"
EMAIL_TAKEN = "the email is taken"
# The same answer whichever list would have matched; nothing is created.
SIGNUP_NOT_OPEN = "sign-up is not open for this email"
# A login with the right password whose email is not confirmed: a code is (normally) on its way.
EMAIL_NOT_VERIFIED_CODE = "EMAIL_NOT_VERIFIED"
EMAIL_NOT_VERIFIED = "confirm your email first; we sent you a code"

Settings = Annotated[ApiSettings, Depends(get_api_settings)]
SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]
Now = Annotated[datetime, Depends(get_now)]
Sender = Annotated[MailSender, Depends(get_mail_sender)]


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def _user_response(
    session: Session, settings: ApiSettings, *, account_id: int, user_id: int
) -> UserResponse:
    found = find_user_response(session, settings=settings, account_id=account_id, user_id=user_id)
    if found is None:
        # The user was written or read in this same transaction; missing now is a bug.
        raise RuntimeError(f"user {user_id} of account {account_id} vanished")
    return found


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    settings: Settings,
    factory: SessionFactory,
    now: Now,
    sender: Sender,
) -> UserResponse:
    """Add a user to the shared account, or to a new personal account of their own (email not
    confirmed yet), and email the user a code.
    Does not log in; sets the `email_verify` cookie for the code screen. Nothing is audited
    until the code is typed.

    An email the settings do not allow is refused (403) and nothing is created. An unverified
    holder of the email is replaced. Counts one try against the client IP's limit (429 over
    it). Over the daily email cap nothing is created (503). The email is sent after the answer.
    Takes tens of milliseconds on purpose (the password hash).
    """
    ip_hash = ip_key(_client_host(request), settings.auth_token_secret.get_secret_value())
    row_id = count_ip_try(factory, ip_hash, now=now, settings=settings)
    # A 403, 409 or 503 is raised inside the transaction, so it rolls everything back; the IP row
    # was committed before and stays.
    with session_scope(factory) as session:
        retry_after = refuse_over_ip_limit(session, ip_hash, row_id, now=now, settings=settings)
        if retry_after is None:
            kind = signup_kind(settings, body.email)
            if kind is None:
                logger.info("sign-up refused: email not allowed")
                raise HTTPException(status.HTTP_403_FORBIDDEN, SIGNUP_NOT_OPEN)
            try:
                user = create_member(
                    session,
                    settings=settings,
                    kind=kind,
                    email=body.email,
                    password=body.password,
                    verified_at=None,
                )
            except EmailTakenError:
                raise HTTPException(status.HTTP_409_CONFLICT, EMAIL_TAKEN) from None
            try:
                message = prepare_verify_email(
                    session,
                    account_id=user.account_id,
                    user_id=user.id,
                    settings=settings,
                    now=now,
                )
            except MailCapReachedError:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, MAIL_UNAVAILABLE) from None
            set_verify_cookie(
                response, settings, user_id=user.id, account_id=user.account_id, now=now
            )
            background_tasks.add_task(send_quietly, sender, message)
            logger.info("signup: account %d, user %d", user.account_id, user.id)
            return _user_response(session, settings, account_id=user.account_id, user_id=user.id)
    logger.info("signup refused: too many tries")
    raise too_many_tries(retry_after)


@router.post(
    "/login",
    response_model=UserResponse,
    responses={status.HTTP_403_FORBIDDEN: {"model": EmailNotVerifiedAnswer}},
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    settings: Settings,
    factory: SessionFactory,
    now: Now,
    sender: Sender,
) -> UserResponse | JSONResponse:
    """Check email and password and set the login cookie, or answer 401.

    Answers 429 without checking the password while the email or the client IP has too many
    recent failures. The right password with an email not confirmed yet answers 403
    `EMAIL_NOT_VERIFIED` without the login cookie: it sets the `email_verify` cookie and emails a
    new code, unless the user got one too recently or the daily cap is reached (the last code
    may still work). Takes tens of milliseconds on purpose (the password hash).
    """
    client_host = _client_host(request)
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
    unverified: tuple[int, int] | None = None
    message: MailMessage | None = None
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
            if user is not None and user.email_verified_at is None:
                # The password matched, so this answer tells a stranger nothing.
                clear_email_failures(session, keys.email_hash, ids.ip_id)
                unverified = (user.account_id, user.id)
                try:
                    message = prepare_verify_email(
                        session,
                        account_id=user.account_id,
                        user_id=user.id,
                        settings=settings,
                        now=now,
                    )
                except (CodeTooSoonError, MailCapReachedError):
                    message = None
            elif user is not None:
                clear_email_failures(session, keys.email_hash, ids.ip_id)
                set_login_cookie(
                    response,
                    settings,
                    user_id=user.id,
                    account_id=user.account_id,
                    token_version=user.token_version,
                )
                logger.info("login: account %d, user %d", user.account_id, user.id)
                return _user_response(
                    session, settings, account_id=user.account_id, user_id=user.id
                )
    if retry_after is not None:
        logger.info("login refused: too many failures")
        raise too_many_tries(retry_after)
    if unverified is not None:
        account_id, user_id = unverified
        answer = EmailNotVerifiedAnswer(detail=EMAIL_NOT_VERIFIED, code=EMAIL_NOT_VERIFIED_CODE)
        refused = JSONResponse(answer.model_dump(), status_code=status.HTTP_403_FORBIDDEN)
        set_verify_cookie(refused, settings, user_id=user_id, account_id=account_id, now=now)
        if message is not None:
            background_tasks.add_task(send_quietly, sender, message)
        logger.info("login refused: email not verified, account %d, user %d", account_id, user_id)
        return refused
    # The failure rows were committed before the password check, so they stay.
    logger.info("login failed")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGIN_FAILED)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, settings: Settings) -> None:
    """Clear the login cookie. The token itself stays valid until it expires."""
    clear_login_cookie(response, settings)
