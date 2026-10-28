"""The two short-lived cookies of "Continue with Google".

`google_flow` ties Google's answer to the browser that started the sign-in: it holds the state,
the nonce and the PKCE verifier. `google_signup` carries a checked Google identity that has no
user yet to the "join the account" step. Both are HS256 JWTs signed with a key derived from the
token secret plus a label, so neither can pass as a login or reset token, and each names its kind
(`typ`), so one cannot pass as the other.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict

from src.api.tokens import TOKEN_ALGORITHM

# Mixed into the token secret, so these cookies' key is never the key of login or reset tokens.
_KEY_LABEL = b"google-sign-in"
FLOW_TYPE = "google_flow"
SIGNUP_TYPE = "google_signup"
FLOW_COOKIE_NAME = "google_flow"
SIGNUP_COOKIE_NAME = "google_signup"
COOKIE_TTL = timedelta(minutes=10)
# Random sizes in bytes. A PKCE verifier must be 43 to 128 characters: 64 bytes give 86.
_STATE_BYTES = 32
_NONCE_BYTES = 32
_VERIFIER_BYTES = 64


class InvalidGoogleCookieError(Exception):
    """The cookie is missing, damaged, signed with another key, of another kind or expired."""


class GoogleFlow(BaseModel):
    """What the browser must bring back from Google: the state, the nonce and the verifier."""

    model_config = ConfigDict(frozen=True)

    state: str
    nonce: str
    verifier: str


class PendingSignup(BaseModel):
    """A checked Google identity with no user yet: Google's `sub` and the verified email."""

    model_config = ConfigDict(frozen=True)

    subject: str
    email: str


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode(), _KEY_LABEL, hashlib.sha256).digest()


def new_flow() -> GoogleFlow:
    """Make a fresh random state, nonce and PKCE verifier."""
    return GoogleFlow(
        state=secrets.token_urlsafe(_STATE_BYTES),
        nonce=secrets.token_urlsafe(_NONCE_BYTES),
        verifier=secrets.token_urlsafe(_VERIFIER_BYTES),
    )


def _encode(claims: dict[str, str], typ: str, *, secret: str, now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    payload = {**claims, "typ": typ, "iat": now, "exp": now + COOKIE_TTL}
    return jwt.encode(payload, _key(secret), algorithm=TOKEN_ALGORITHM)


def _decode(
    value: str, typ: str, names: tuple[str, ...], *, secret: str, now: datetime
) -> dict[str, Any]:
    error = InvalidGoogleCookieError("the Google sign-in cookie is not valid")
    try:
        # The expiry is checked against `now` below, so tests can move the clock.
        payload = jwt.decode(
            value,
            _key(secret),
            algorithms=[TOKEN_ALGORITHM],
            options={
                "require": [*names, "typ", "iat", "exp"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
    except jwt.PyJWTError as caught:
        raise error from caught
    exp = payload["exp"]
    if (
        payload["typ"] != typ
        or type(exp) is not int
        or exp <= now.timestamp()
        or not all(isinstance(payload[name], str) and payload[name] for name in names)
    ):
        raise error
    return payload


def issue_flow_cookie(flow: GoogleFlow, *, secret: str, now: datetime) -> str:
    """Return the signed `google_flow` value for `flow`, valid ten minutes after `now`."""
    return _encode(flow.model_dump(), FLOW_TYPE, secret=secret, now=now)


def read_flow_cookie(value: str, *, secret: str, now: datetime) -> GoogleFlow:
    """Verify a `google_flow` value and return its flow; every problem raises
    `InvalidGoogleCookieError`, whose message never repeats the value."""
    names = ("state", "nonce", "verifier")
    payload = _decode(value, FLOW_TYPE, names, secret=secret, now=now)
    return GoogleFlow(state=payload["state"], nonce=payload["nonce"], verifier=payload["verifier"])


def issue_signup_cookie(pending: PendingSignup, *, secret: str, now: datetime) -> str:
    """Return the signed `google_signup` value for `pending`, valid ten minutes after `now`."""
    return _encode(
        {"sub": pending.subject, "email": pending.email}, SIGNUP_TYPE, secret=secret, now=now
    )


def read_signup_cookie(value: str, *, secret: str, now: datetime) -> PendingSignup:
    """Verify a `google_signup` value and return the identity; every problem raises
    `InvalidGoogleCookieError`, whose message never repeats the value."""
    payload = _decode(value, SIGNUP_TYPE, ("sub", "email"), secret=secret, now=now)
    return PendingSignup(subject=payload["sub"], email=payload["email"])
