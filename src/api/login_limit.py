"""Login limit: refuse a login when the typed email or the client IP failed too often recently.

Every try first writes one `login_failures` row per key (email, IP) and commits it, then counts
the rows inside the window, its own included. Tries that run at the same time therefore see each
other, and at most the limit reach the password check. Neither value is stored: the key is an
HMAC-SHA256 with a key derived from the token secret. A refused try deletes its own rows again
and costs no hashing; a good login deletes the email's rows and its own IP row.
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


class FailureIds(BaseModel):
    """The ids of the two rows one try wrote, so it can take them back."""

    model_config = ConfigDict(frozen=True)

    email_id: int
    ip_id: int


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


def _limits(settings: ApiSettings) -> dict[LoginKeyKind, int]:
    return {
        LoginKeyKind.EMAIL: settings.login_limit_per_email,
        LoginKeyKind.IP: settings.login_limit_per_ip,
    }


def over_limit(session: Session, keys: LoginKeys, *, now: datetime, settings: ApiSettings) -> bool:
    """True if the email or the IP has more failures inside the window than its limit.

    Called after the try's own rows are committed, so they are part of the count.
    """
    window = timedelta(minutes=settings.login_limit_window_minutes)
    for kind, key_hash in _pairs(keys):
        limit = _limits(settings)[kind]
        rows = session.scalars(
            select(LoginFailure.id)
            .where(
                LoginFailure.key_kind == kind,
                LoginFailure.key_hash == key_hash,
                LoginFailure.created_at > now - window,
            )
            .limit(limit + 1)
        ).all()
        if len(rows) > limit:
            return True
    return False


def retry_after_seconds(
    session: Session, keys: LoginKeys, *, now: datetime, settings: ApiSettings
) -> int | None:
    """Seconds until a login with `keys` is allowed again, or None if it is allowed now."""
    window = timedelta(minutes=settings.login_limit_window_minutes)
    limits = _limits(settings)
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


def reserve_failure(
    session: Session, keys: LoginKeys, *, now: datetime, window_minutes: int
) -> FailureIds:
    """Write one failure row per key at `now`, delete every row older than the window, and
    return the new rows' ids.

    Call it in its own transaction and commit before counting: other tries see the rows only
    once they are committed, and a failed try ends in an error, which rolls back.
    """
    session.execute(
        delete(LoginFailure).where(
            LoginFailure.created_at <= now - timedelta(minutes=window_minutes)
        )
    )
    email_row, ip_row = (
        LoginFailure(key_kind=kind, key_hash=key_hash, created_at=now, updated_at=now)
        for kind, key_hash in _pairs(keys)
    )
    session.add_all([email_row, ip_row])
    session.flush()
    return FailureIds(email_id=email_row.id, ip_id=ip_row.id)


def release_failure(session: Session, ids: FailureIds) -> None:
    """Delete the two rows of a try that was refused, so a refused try is not counted."""
    session.execute(delete(LoginFailure).where(LoginFailure.id.in_([ids.email_id, ids.ip_id])))


def clear_email_failures(session: Session, keys: LoginKeys, ids: FailureIds) -> None:
    """After a good login, delete the email key's failures and this try's own IP row. Earlier IP
    failures stay, or an attacker could reset the IP count by logging in to their own account
    between guesses."""
    session.execute(
        delete(LoginFailure).where(
            (
                (LoginFailure.key_kind == LoginKeyKind.EMAIL)
                & (LoginFailure.key_hash == keys.email_hash)
            )
            | (LoginFailure.id == ids.ip_id)
        )
    )
