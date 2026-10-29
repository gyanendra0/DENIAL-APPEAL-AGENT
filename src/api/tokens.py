"""Login tokens: a JWT signed with HS256 that names the user, the user's account and the user's
token version (raised by a password change, so older tokens stop working)."""

from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel, ConfigDict

TOKEN_ALGORITHM = "HS256"  # noqa: S105  (an algorithm name, not a password)
REQUIRED_CLAIMS = ("sub", "acct", "ver", "iat", "exp")


class InvalidTokenError(Exception):
    """The token is missing, damaged, signed with another key, of another algorithm or expired."""


class TokenClaims(BaseModel):
    """What a verified token says: who the user is, which account they belong to, the user's
    token version when it was issued, and when it was issued (the login, to the second)."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    account_id: int
    token_version: int
    issued_at: datetime


def issue_token(
    *,
    user_id: int,
    account_id: int,
    token_version: int,
    secret: str,
    ttl_minutes: int,
    now: datetime,
) -> str:
    """Return a signed token for one user, valid `ttl_minutes` after `now` (timezone-aware)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    payload = {
        # PyJWT requires `sub` to be a string.
        "sub": str(user_id),
        "acct": account_id,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, secret, algorithm=TOKEN_ALGORITHM)


def read_token(token: str, *, secret: str) -> TokenClaims:
    """Verify `token` against `secret` and the current time, and return its claims.

    Every problem raises `InvalidTokenError`; the message never repeats the token.
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[TOKEN_ALGORITHM],
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.PyJWTError as error:
        raise InvalidTokenError("the login token is not valid") from error
    sub, acct, ver, iat = payload["sub"], payload["acct"], payload["ver"], payload["iat"]
    # A JSON number is never a bool in the token we issue; refuse one so `true` is not user 1.
    if (
        not (isinstance(sub, str) and sub.isdecimal())
        or type(acct) is not int
        or type(ver) is not int
        or ver < 0
        or type(iat) is not int
    ):
        raise InvalidTokenError("the login token is not valid")
    return TokenClaims(
        user_id=int(sub),
        account_id=acct,
        token_version=ver,
        issued_at=datetime.fromtimestamp(iat, UTC),
    )
