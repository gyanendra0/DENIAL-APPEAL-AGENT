"""The `email_verify` cookie: which user the "enter your code" screen is for.

Nobody is logged in while an email is unverified, so this signed cookie names the account and
the user instead. It is an HS256 JWT signed with a key derived from the token secret plus its
own label, so it can never pass as a login, reset or Google token, and it names its kind
(`typ`). It is sent only to the `/auth/verify-email` routes.
"""

import hashlib
import hmac
from datetime import datetime, timedelta

import jwt
from fastapi import Response
from pydantic import BaseModel, ConfigDict

from src.api.tokens import TOKEN_ALGORITHM
from src.config.settings import ApiSettings

# Mixed into the token secret, so this cookie's key is never the key of another token.
_KEY_LABEL = b"email-verify"
VERIFY_TYPE = "emailverify"
VERIFY_COOKIE_NAME = "email_verify"
VERIFY_COOKIE_PATH = "/auth/verify-email"
COOKIE_TTL = timedelta(hours=24)
REQUIRED_CLAIMS = ("sub", "acct", "typ", "iat", "exp")


class InvalidVerifyCookieError(Exception):
    """The cookie is damaged, signed with another key, of another kind or expired."""


class VerifyClaims(BaseModel):
    """The user whose email the code screen confirms."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    account_id: int


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode(), _KEY_LABEL, hashlib.sha256).digest()


def issue_verify_cookie(*, user_id: int, account_id: int, secret: str, now: datetime) -> str:
    """Return the signed `email_verify` value for one user, valid 24 hours after `now`."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    payload = {
        "sub": str(user_id),
        "acct": account_id,
        "typ": VERIFY_TYPE,
        "iat": now,
        "exp": now + COOKIE_TTL,
    }
    return jwt.encode(payload, _key(secret), algorithm=TOKEN_ALGORITHM)


def read_verify_cookie(value: str, *, secret: str, now: datetime) -> VerifyClaims:
    """Verify an `email_verify` value and return its user; every problem raises
    `InvalidVerifyCookieError`, whose message never repeats the value."""
    error = InvalidVerifyCookieError("the email verification cookie is not valid")
    try:
        # The expiry is checked against `now` below, so tests can move the clock.
        payload = jwt.decode(
            value,
            _key(secret),
            algorithms=[TOKEN_ALGORITHM],
            options={"require": list(REQUIRED_CLAIMS), "verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError as caught:
        raise error from caught
    sub, acct, typ, exp = (payload[k] for k in ("sub", "acct", "typ", "exp"))
    # A JSON number is never a bool in the cookie we issue; refuse one so `true` is not user 1.
    if (
        typ != VERIFY_TYPE
        or not (isinstance(sub, str) and sub.isdecimal())
        or type(acct) is not int
        or type(exp) is not int
        or exp <= now.timestamp()
    ):
        raise error
    return VerifyClaims(user_id=int(sub), account_id=acct)


def set_verify_cookie(
    response: Response, settings: ApiSettings, *, user_id: int, account_id: int, now: datetime
) -> None:
    """Put a new `email_verify` cookie for the user on `response`."""
    value = issue_verify_cookie(
        user_id=user_id,
        account_id=account_id,
        secret=settings.auth_token_secret.get_secret_value(),
        now=now,
    )
    response.set_cookie(
        VERIFY_COOKIE_NAME,
        value,
        max_age=int(COOKIE_TTL.total_seconds()),
        path=VERIFY_COOKIE_PATH,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def delete_verify_cookie(response: Response, settings: ApiSettings) -> None:
    """Clear the `email_verify` cookie (the email is confirmed)."""
    response.delete_cookie(
        VERIFY_COOKIE_NAME,
        path=VERIFY_COOKIE_PATH,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )
