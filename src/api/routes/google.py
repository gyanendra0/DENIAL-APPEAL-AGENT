""" "Continue with Google": start, Google's return, and the "join the account" step.

The browser is sent to Google by a full page load, so the start and the callback answer with
redirects, never with JSON: a refusal goes to the login screen with a fixed code
(`?google=<code>`), never with what Google sent. Nothing is written on a refusal. Without
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` every `/auth/google/` route answers 404.
"""

import logging
import secrets
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from src.api.dependencies import DbSession, get_api_settings, get_google, get_now
from src.api.google_cookies import (
    COOKIE_TTL,
    FLOW_COOKIE_NAME,
    SIGNUP_COOKIE_NAME,
    InvalidGoogleCookieError,
    PendingSignup,
    issue_flow_cookie,
    issue_signup_cookie,
    new_flow,
    read_flow_cookie,
    read_signup_cookie,
)
from src.api.google_identity import (
    GoogleRefusal,
    GoogleRefusedError,
    create_google_member,
    sign_in_with_google,
)
from src.api.google_oidc import GoogleOidc, GoogleSignInError
from src.api.login_cookie import set_login_cookie
from src.api.routes.auth import EMAIL_TAKEN, SIGNUP_NOT_OPEN
from src.api.schemas import AuthOptions, GoogleSignupInfo, GoogleSignupRequest, UserResponse
from src.api.signup import EmailTakenError, record_joined, signup_kind
from src.api.user_lookup import find_user_response
from src.config.settings import ApiSettings
from src.db.models import AccountKind

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

CALLBACK_PATH = "/auth/google/callback"
SIGNUP_PATH = "/auth/google/signup"
# Screens of the frontend the browser is sent on to.
LOGIN_SCREEN = "/app/login"
HOME_SCREEN = "/app/worklist"
JOIN_ACCOUNT_SCREEN = "/app/google/name-account"
# Far above anything Google sends; longer values are refused before any work.
MAX_CALLBACK_VALUE_CHARACTERS = 2048

SIGNUP_EXPIRED = "the Google sign-up has expired; continue with Google again"
GOOGLE_LINKED_ELSEWHERE = "this Google account is linked to another login"

Settings = Annotated[ApiSettings, Depends(get_api_settings)]
Google = Annotated[GoogleOidc, Depends(get_google)]
Now = Annotated[datetime, Depends(get_now)]


def _secret(settings: ApiSettings) -> str:
    return settings.auth_token_secret.get_secret_value()


def _set_cookie(
    response: Response,
    settings: ApiSettings,
    name: str,
    value: str,
    *,
    path: str,
    samesite: Literal["lax", "strict"],
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=int(COOKIE_TTL.total_seconds()),
        path=path,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=samesite,
    )


def _delete_cookie(
    response: Response,
    settings: ApiSettings,
    name: str,
    *,
    path: str,
    samesite: Literal["lax", "strict"],
) -> None:
    response.delete_cookie(
        name, path=path, secure=settings.auth_cookie_secure, httponly=True, samesite=samesite
    )


def _redirect(url: str, settings: ApiSettings) -> RedirectResponse:
    """A 303 to `url` that also ends the flow cookie: it is used once, whatever happens."""
    response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    _delete_cookie(response, settings, FLOW_COOKIE_NAME, path=CALLBACK_PATH, samesite="lax")
    return response


def _refuse(reason: GoogleRefusal, settings: ApiSettings) -> RedirectResponse:
    logger.info("google sign-in refused: %s", reason.value)
    return _redirect(f"{LOGIN_SCREEN}?google={reason.value}", settings)


def _usable(value: str | None) -> bool:
    return value is not None and 0 < len(value) <= MAX_CALLBACK_VALUE_CHARACTERS


@router.get("/options")
def options(settings: Settings) -> AuthOptions:
    """Say which ways in besides email and password are on, so the screens show only those."""
    return AuthOptions(google=settings.google_enabled)


@router.get("/google/start")
def start(google: Google, settings: Settings, now: Now) -> RedirectResponse:
    """Send the browser to Google with a fresh state, nonce and PKCE challenge.

    They are kept in the signed `google_flow` cookie, `SameSite=Lax` so that the browser sends
    it back on Google's return (a top-level page load started on Google's site).
    """
    flow = new_flow()
    response = RedirectResponse(
        google.authorization_url(flow), status_code=status.HTTP_303_SEE_OTHER
    )
    value = issue_flow_cookie(flow, secret=_secret(settings), now=now)
    _set_cookie(response, settings, FLOW_COOKIE_NAME, value, path=CALLBACK_PATH, samesite="lax")
    return response


@router.get("/google/callback")
def callback(
    request: Request,
    google: Google,
    settings: Settings,
    session: DbSession,
    now: Now,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish the sign-in Google sent the browser back from.

    Checks the flow cookie and the state, swaps the code for an ID token and checks it, then
    logs the user in (linking the Google account first when allowed), or sends a Google account
    with no user to the "join the account" screen if its email may sign up. Calls Google, so it
    can take a second or more.
    """
    value = request.cookies.get(FLOW_COOKIE_NAME)
    if value is None:
        return _refuse(GoogleRefusal.EXPIRED, settings)
    try:
        flow = read_flow_cookie(value, secret=_secret(settings), now=now)
    except InvalidGoogleCookieError:
        return _refuse(GoogleRefusal.EXPIRED, settings)
    if state is None or not _usable(state):
        return _refuse(GoogleRefusal.FAILED, settings)
    if not secrets.compare_digest(state.encode(), flow.state.encode()):
        return _refuse(GoogleRefusal.FAILED, settings)
    # `error` is set when the user cancels at Google; its text is not shown or logged.
    if error is not None or code is None or not _usable(code):
        return _refuse(GoogleRefusal.FAILED, settings)
    try:
        id_token = google.swap_code(code, flow.verifier)
        identity = google.verify_id_token(id_token, nonce=flow.nonce, now=now)
    except GoogleSignInError:
        return _refuse(GoogleRefusal.FAILED, settings)
    try:
        user = sign_in_with_google(session, identity)
    except GoogleRefusedError as refused:
        return _refuse(refused.reason, settings)
    if user is None:
        if signup_kind(settings, identity.email) is None:
            return _refuse(GoogleRefusal.NOT_ALLOWED, settings)
        pending = PendingSignup(subject=identity.subject, email=identity.email)
        response = _redirect(JOIN_ACCOUNT_SCREEN, settings)
        signup_value = issue_signup_cookie(pending, secret=_secret(settings), now=now)
        # The next request is the screen's same-origin `fetch`, so `Strict` is enough.
        _set_cookie(
            response,
            settings,
            SIGNUP_COOKIE_NAME,
            signup_value,
            path=SIGNUP_PATH,
            samesite="strict",
        )
        logger.info("google sign-in: no user yet, asking to join the account")
        return response
    response = _redirect(HOME_SCREEN, settings)
    set_login_cookie(
        response,
        settings,
        user_id=user.id,
        account_id=user.account_id,
        token_version=user.token_version,
    )
    logger.info("google sign-in: account %d, user %d", user.account_id, user.id)
    return response


def _pending_signup(request: Request, settings: ApiSettings, now: datetime) -> PendingSignup:
    value = request.cookies.get(SIGNUP_COOKIE_NAME)
    if value is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SIGNUP_EXPIRED)
    try:
        return read_signup_cookie(value, secret=_secret(settings), now=now)
    except InvalidGoogleCookieError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SIGNUP_EXPIRED) from None


@router.get("/google/signup")
def signup_info(
    request: Request, _google: Google, settings: Settings, now: Now
) -> GoogleSignupInfo:
    """Say which verified Google email the pending sign-up will use and which kind of account it
    will join (with the shared account's name; a personal account's is never shown), or 401 if
    there is none. An email no longer allowed reads as personal or shared like any other; the
    sign-up itself refuses it."""
    email = _pending_signup(request, settings, now).email
    if signup_kind(settings, email) is AccountKind.PERSONAL:
        return GoogleSignupInfo(email=email, account_kind=AccountKind.PERSONAL, account_name=None)
    return GoogleSignupInfo(
        email=email, account_kind=AccountKind.SHARED, account_name=settings.shared_account_name
    )


@router.post("/google/signup", status_code=status.HTTP_201_CREATED)
def signup(
    body: GoogleSignupRequest,
    request: Request,
    response: Response,
    _google: Google,
    settings: Settings,
    session: DbSession,
    now: Now,
) -> UserResponse:
    """Add the user (no password) to the shared account or a new personal account with the
    Google link, audit that the user created or joined the account, and log that user in. The
    email and the Google account come from the cookie; the body is empty. An email the settings
    no longer allow is refused (403) and nothing is created."""
    pending = _pending_signup(request, settings, now)
    kind = signup_kind(settings, pending.email)
    if kind is None:
        logger.info("google sign-up refused: email not allowed")
        raise HTTPException(status.HTTP_403_FORBIDDEN, SIGNUP_NOT_OPEN)
    try:
        user = create_google_member(
            session,
            settings=settings,
            kind=kind,
            subject=pending.subject,
            email=pending.email,
            now=now,
        )
    except EmailTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, EMAIL_TAKEN) from None
    except GoogleRefusedError:
        raise HTTPException(status.HTTP_409_CONFLICT, GOOGLE_LINKED_ELSEWHERE) from None
    record_joined(session, user)
    _delete_cookie(response, settings, SIGNUP_COOKIE_NAME, path=SIGNUP_PATH, samesite="strict")
    set_login_cookie(
        response,
        settings,
        user_id=user.id,
        account_id=user.account_id,
        token_version=user.token_version,
    )
    logger.info("google sign-up: account %d, user %d", user.account_id, user.id)
    found = find_user_response(
        session, settings=settings, account_id=user.account_id, user_id=user.id
    )
    if found is None:
        # The user was written in this same transaction; missing now is a bug.
        raise RuntimeError(f"user {user.id} of account {user.account_id} vanished")
    return found
