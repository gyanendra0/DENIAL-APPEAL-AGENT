"""Login limit: refuse a login when the typed email or the client IP failed too often recently.

A failed login writes one `login_failures` row per key (email, IP). Neither value is stored:
the key is an HMAC-SHA256 with a key derived from the token secret. The caller checks the
limit before the password, so a refused try costs no hashing and writes nothing.
"""

import hashlib
import hmac
import ipaddress
import math
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.config.settings import ApiSettings
from src.db.models import LoginFailure, LoginKeyKind

TOO_MANY_LOGINS = "too many failed logins; try again later"

# Mixed into the token secret, so the limit's hash key is never the key that signs tokens.
_KEY_LABEL = b"login-rate-limit"
# One home or server usually gets a whole IPv6 /64, so single addresses would be easy to dodge.
_IPV6_PREFIX = 64
# Counted for a request whose client address the server does not know.
_UNKNOWN_HOST = "unknown"


class LoginKeys(BaseModel):
    """The two keys one login is counted against, as lowercase hex HMACs."""

    model_config = ConfigDict(frozen=True)

    email_hash: str
    ip_hash: str


def _client_value(client_host: str | None) -> str:
    """The text counted for `client_host`: the IPv4 address, or the IPv6 /64 network."""
    if client_host is None:
        return _UNKNOWN_HOST
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        return client_host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((address, _IPV6_PREFIX), strict=False))
    return str(address)


def login_keys(email: str, client_host: str | None, secret: str) -> LoginKeys:
    """Hash the typed email (already trimmed and lowercased) and the client IP."""
    key = hmac.new(secret.encode(), _KEY_LABEL, hashlib.sha256).digest()

    def digest(value: str) -> str:
        return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()

    return LoginKeys(email_hash=digest(email), ip_hash=digest(_client_value(client_host)))


def _pairs(keys: LoginKeys) -> tuple[tuple[LoginKeyKind, str], tuple[LoginKeyKind, str]]:
    return (LoginKeyKind.EMAIL, keys.email_hash), (LoginKeyKind.IP, keys.ip_hash)


def retry_after_seconds(
    session: Session, keys: LoginKeys, *, now: datetime, settings: ApiSettings
) -> int | None:
    """Seconds until a login with `keys` is allowed again, or None if it is allowed now."""
    window = timedelta(minutes=settings.login_limit_window_minutes)
    limits = {
        LoginKeyKind.EMAIL: settings.login_limit_per_email,
        LoginKeyKind.IP: settings.login_limit_per_ip,
    }
    waits: list[int] = []
    for kind, key_hash in _pairs(keys):
        limit = limits[kind]
        # The newest `limit` failures; the oldest of them has to leave the window first.
        newest = session.scalars(
            select(LoginFailure.created_at)
            .where(
                LoginFailure.key_kind == kind,
                LoginFailure.key_hash == key_hash,
                LoginFailure.created_at > now - window,
            )
            .order_by(LoginFailure.created_at.desc())
            .limit(limit)
        ).all()
        if len(newest) == limit:
            seconds = (newest[-1] + window - now).total_seconds()
            waits.append(max(1, math.ceil(seconds)))
    return max(waits) if waits else None


def record_failure(
    session: Session, keys: LoginKeys, *, now: datetime, window_minutes: int
) -> None:
    """Write one failure row per key at `now`, and delete every row older than the window.

    Call it in its own transaction: the request that failed ends in an error, which rolls back.
    """
    session.execute(
        delete(LoginFailure).where(
            LoginFailure.created_at <= now - timedelta(minutes=window_minutes)
        )
    )
    session.add_all(
        LoginFailure(key_kind=kind, key_hash=key_hash, created_at=now, updated_at=now)
        for kind, key_hash in _pairs(keys)
    )


def clear_email_failures(session: Session, keys: LoginKeys) -> None:
    """Delete the email key's failures after a good login. The IP key's failures stay, or an
    attacker could reset the IP count by logging in to their own account between guesses."""
    session.execute(
        delete(LoginFailure).where(
            LoginFailure.key_kind == LoginKeyKind.EMAIL,
            LoginFailure.key_hash == keys.email_hash,
        )
    )
