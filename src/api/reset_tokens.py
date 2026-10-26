"""Password reset tokens: a signed envelope in the emailed link, and its stored SHA-256.

The link carries an HS256 JWT signed with a key derived from the token secret plus a label, so
it can never pass as a login token and a login token can never pass as it. Its claims name the
account and the user, so the stored row is found with an `account_id` filter although nobody is
logged in. The random part (`jti`) is stored only as its SHA-256. Only the user's newest row
works, so asking again makes older links useless; a used row is deleted.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

import jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.api.tokens import TOKEN_ALGORITHM
from src.db.models import PasswordResetToken
from src.db.query import for_account

# Mixed into the token secret, so the reset key is never the key that signs login tokens.
_KEY_LABEL = b"password-reset"
RESET_TYPE = "pwreset"
REQUIRED_CLAIMS = ("sub", "acct", "jti", "typ", "iat", "exp")
# 32 random bytes: guessing one is impossible, so a fast hash is enough to store it.
_RANDOM_BYTES = 32
# The per-email cap counts the rows of this last stretch of time.
CAP_WINDOW = timedelta(hours=1)


class InvalidResetTokenError(Exception):
    """The reset token is damaged, signed with another key, of another kind or expired."""


class ResetClaims(BaseModel):
    """What a verified reset token says: whose password it may set, and its random part."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    account_id: int
    token_id: str


class IssuedResetToken(BaseModel):
    """A new reset token for the link, and what is stored of it."""

    model_config = ConfigDict(frozen=True)

    token: str
    token_hash: str
    expires_at: datetime


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode(), _KEY_LABEL, hashlib.sha256).digest()


def hash_token_id(token_id: str) -> str:
    """The SHA-256 of a token's random part, as lowercase hex (the stored form)."""
    return hashlib.sha256(token_id.encode()).hexdigest()


def issue_reset_token(
    *, user_id: int, account_id: int, secret: str, ttl_minutes: int, now: datetime
) -> IssuedResetToken:
    """Return a new signed reset token for one user, valid `ttl_minutes` after `now`."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    token_id = secrets.token_urlsafe(_RANDOM_BYTES)
    expires_at = now + timedelta(minutes=ttl_minutes)
    payload = {
        "sub": str(user_id),
        "acct": account_id,
        "jti": token_id,
        "typ": RESET_TYPE,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, _key(secret), algorithm=TOKEN_ALGORITHM)
    return IssuedResetToken(token=token, token_hash=hash_token_id(token_id), expires_at=expires_at)


def read_reset_token(token: str, *, secret: str, now: datetime) -> ResetClaims:
    """Verify `token` against `secret` and `now`, and return its claims.

    Every problem raises `InvalidResetTokenError`; the message never repeats the token.
    """
    error = InvalidResetTokenError("the reset token is not valid")
    try:
        # The expiry is checked against `now` below, so tests can move the clock.
        payload = jwt.decode(
            token,
            _key(secret),
            algorithms=[TOKEN_ALGORITHM],
            options={"require": list(REQUIRED_CLAIMS), "verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError as caught:
        raise error from caught
    sub, acct, jti, typ, exp = (payload[k] for k in ("sub", "acct", "jti", "typ", "exp"))
    # A JSON number is never a bool in the token we issue; refuse one so `true` is not user 1.
    if (
        typ != RESET_TYPE
        or not (isinstance(sub, str) and sub.isdecimal())
        or type(acct) is not int
        or not (isinstance(jti, str) and jti)
        or type(exp) is not int
        or exp <= now.timestamp()
    ):
        raise error
    return ResetClaims(user_id=int(sub), account_id=acct, token_id=jti)


def add_reset_token(
    session: Session, *, account_id: int, user_id: int, issued: IssuedResetToken, now: datetime
) -> None:
    """Store the hash of a newly issued token. Does not commit."""
    session.add(
        PasswordResetToken(
            account_id=account_id,
            user_id=user_id,
            token_hash=issued.token_hash,
            expires_at=issued.expires_at,
            created_at=now,
            updated_at=now,
        )
    )


def count_recent_reset_tokens(
    session: Session, *, account_id: int, user_id: int, now: datetime
) -> int:
    """How many tokens the user was given inside the cap window."""
    found = session.scalar(
        select(func.count())
        .select_from(PasswordResetToken)
        .where(
            PasswordResetToken.account_id == account_id,
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.created_at > now - CAP_WINDOW,
        )
    )
    return found or 0


def delete_stale_reset_tokens(
    session: Session, *, account_id: int, user_id: int, now: datetime
) -> None:
    """Delete the user's rows that are expired and older than the cap window (no longer
    needed to open a link or to count toward the cap)."""
    session.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.account_id == account_id,
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.expires_at <= now,
            PasswordResetToken.created_at <= now - CAP_WINDOW,
        )
    )


def delete_reset_tokens(session: Session, *, account_id: int, user_id: int) -> None:
    """Delete every reset token of the user (used, or the password changed)."""
    session.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.account_id == account_id, PasswordResetToken.user_id == user_id
        )
    )


def usable_reset_token(session: Session, claims: ResetClaims, *, now: datetime) -> bool:
    """True if the token is the user's newest one and has not expired."""
    newest = session.scalars(
        for_account(PasswordResetToken, claims.account_id)
        .where(PasswordResetToken.user_id == claims.user_id)
        .order_by(PasswordResetToken.id.desc())
        .limit(1)
    ).one_or_none()
    return (
        newest is not None
        and secrets.compare_digest(newest.token_hash, hash_token_id(claims.token_id))
        and newest.expires_at > now
    )
