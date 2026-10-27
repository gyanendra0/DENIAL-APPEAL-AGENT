"""Login limit: refuse a login when the typed email or the client IP failed too often recently.

Every try first writes one `login_failures` row per key (email, IP) and commits it, then counts
the rows inside the window, its own included. Tries that run at the same time therefore see each
other, and at most the limit reach the password check. Neither value is stored: the key is an
HMAC-SHA256 with a key derived from the token secret. A refused try deletes its own rows again
and costs no hashing; a good login deletes the email's rows and its own IP row. The password
reset routes and the sign-up and code routes count by IP only (the `ip_` functions), so their
answer never depends on the email.
"""

import hashlib
import hmac
import ipaddress
import math
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import ApiSettings
from src.db.models import LoginFailure, LoginKeyKind
from src.db.session import session_scope

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


def _digest(value: str, secret: str) -> str:
    key = hmac.new(secret.encode(), _KEY_LABEL, hashlib.sha256).digest()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def login_keys(email: str, client_host: str | None, secret: str) -> LoginKeys:
    """Hash the typed email (already trimmed and lowercased) and the client IP."""
    return LoginKeys(email_hash=_digest(email, secret), ip_hash=ip_key(client_host, secret))


def ip_key(client_host: str | None, secret: str) -> str:
    """Hash the client IP alone, for a route that counts tries by IP only."""
    return _digest(_client_value(client_host), secret)


def _pairs(keys: LoginKeys) -> tuple[tuple[LoginKeyKind, str], tuple[LoginKeyKind, str]]:
    return (LoginKeyKind.EMAIL, keys.email_hash), (LoginKeyKind.IP, keys.ip_hash)


def _limits(settings: ApiSettings) -> dict[LoginKeyKind, int]:
    return {
        LoginKeyKind.EMAIL: settings.login_limit_per_email,
        LoginKeyKind.IP: settings.login_limit_per_ip,
    }


def _over(
    session: Session, kind: LoginKeyKind, key_hash: str, limit: int, *, now: datetime, window: int
) -> bool:
    rows = session.scalars(
        select(LoginFailure.id)
        .where(
            LoginFailure.key_kind == kind,
            LoginFailure.key_hash == key_hash,
            LoginFailure.created_at > now - timedelta(minutes=window),
        )
        .limit(limit + 1)
    ).all()
    return len(rows) > limit


def _wait(
    session: Session, kind: LoginKeyKind, key_hash: str, limit: int, *, now: datetime, window: int
) -> int | None:
    # The newest `limit` failures; the oldest of them has to leave the window first.
    newest = session.scalars(
        select(LoginFailure.created_at)
        .where(
            LoginFailure.key_kind == kind,
            LoginFailure.key_hash == key_hash,
            LoginFailure.created_at > now - timedelta(minutes=window),
        )
        .order_by(LoginFailure.created_at.desc())
        .limit(limit)
    ).all()
    if len(newest) < limit:
        return None
    seconds = (newest[-1] + timedelta(minutes=window) - now).total_seconds()
    return max(1, math.ceil(seconds))


def over_limit(session: Session, keys: LoginKeys, *, now: datetime, settings: ApiSettings) -> bool:
    """True if the email or the IP has more failures inside the window than its limit.

    Called after the try's own rows are committed, so they are part of the count.
    """
    window = settings.login_limit_window_minutes
    limits = _limits(settings)
    return any(
        _over(session, kind, key_hash, limits[kind], now=now, window=window)
        for kind, key_hash in _pairs(keys)
    )


def retry_after_seconds(
    session: Session, keys: LoginKeys, *, now: datetime, settings: ApiSettings
) -> int | None:
    """Seconds until a login with `keys` is allowed again, or None if it is allowed now."""
    window = settings.login_limit_window_minutes
    limits = _limits(settings)
    waits = [
        wait
        for kind, key_hash in _pairs(keys)
        if (wait := _wait(session, kind, key_hash, limits[kind], now=now, window=window))
        is not None
    ]
    return max(waits) if waits else None


def ip_over_limit(session: Session, ip_hash: str, *, now: datetime, settings: ApiSettings) -> bool:
    """`over_limit` for the IP key alone."""
    return _over(
        session,
        LoginKeyKind.IP,
        ip_hash,
        settings.login_limit_per_ip,
        now=now,
        window=settings.login_limit_window_minutes,
    )


def ip_retry_after_seconds(
    session: Session, ip_hash: str, *, now: datetime, settings: ApiSettings
) -> int | None:
    """`retry_after_seconds` for the IP key alone."""
    return _wait(
        session,
        LoginKeyKind.IP,
        ip_hash,
        settings.login_limit_per_ip,
        now=now,
        window=settings.login_limit_window_minutes,
    )


def _delete_old(session: Session, *, now: datetime, window_minutes: int) -> None:
    session.execute(
        delete(LoginFailure).where(
            LoginFailure.created_at <= now - timedelta(minutes=window_minutes)
        )
    )


def reserve_failure(
    session: Session, keys: LoginKeys, *, now: datetime, window_minutes: int
) -> FailureIds:
    """Write one failure row per key at `now`, delete every row older than the window, and
    return the new rows' ids.

    Call it in its own transaction and commit before counting: other tries see the rows only
    once they are committed, and a failed try ends in an error, which rolls back.
    """
    _delete_old(session, now=now, window_minutes=window_minutes)
    email_row, ip_row = (
        LoginFailure(key_kind=kind, key_hash=key_hash, created_at=now, updated_at=now)
        for kind, key_hash in _pairs(keys)
    )
    session.add_all([email_row, ip_row])
    session.flush()
    return FailureIds(email_id=email_row.id, ip_id=ip_row.id)


def reserve_ip_failure(
    session: Session, ip_hash: str, *, now: datetime, window_minutes: int
) -> int:
    """`reserve_failure` for the IP key alone; returns the new row's id."""
    _delete_old(session, now=now, window_minutes=window_minutes)
    row = LoginFailure(key_kind=LoginKeyKind.IP, key_hash=ip_hash, created_at=now, updated_at=now)
    session.add(row)
    session.flush()
    return row.id


def release_failure(session: Session, ids: FailureIds) -> None:
    """Delete the two rows of a try that was refused, so a refused try is not counted."""
    session.execute(delete(LoginFailure).where(LoginFailure.id.in_([ids.email_id, ids.ip_id])))


def release_ip_failure(session: Session, row_id: int) -> None:
    """Delete the IP row of a try that was refused."""
    session.execute(delete(LoginFailure).where(LoginFailure.id == row_id))


def clear_email_failures(session: Session, email_hash: str, ip_row_id: int) -> None:
    """After a good login, delete the email key's failures and this try's own IP row. Earlier IP
    failures stay, or an attacker could reset the IP count by logging in to their own account
    between guesses."""
    session.execute(
        delete(LoginFailure).where(
            ((LoginFailure.key_kind == LoginKeyKind.EMAIL) & (LoginFailure.key_hash == email_hash))
            | (LoginFailure.id == ip_row_id)
        )
    )


def too_many_tries(retry_after: int, detail: str = TOO_MANY_LOGINS) -> HTTPException:
    """The 429 answer, telling the client how many seconds to wait."""
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail,
        headers={"Retry-After": str(retry_after)},
    )


def count_ip_try(
    factory: sessionmaker[Session], ip_hash: str, *, now: datetime, settings: ApiSettings
) -> int:
    """Write and commit one IP failure row for this try (`reserve_ip_failure` in its own
    transaction), and return its id. The row stays unless the try is refused or succeeds."""
    with session_scope(factory) as session:
        return reserve_ip_failure(
            session, ip_hash, now=now, window_minutes=settings.login_limit_window_minutes
        )


def refuse_over_ip_limit(
    session: Session, ip_hash: str, row_id: int, *, now: datetime, settings: ApiSettings
) -> int | None:
    """If the IP is over its limit, take back this try's row and return the seconds to wait;
    else return None. Call it after `count_ip_try`, in the try's second transaction."""
    if not ip_over_limit(session, ip_hash, now=now, settings=settings):
        return None
    release_ip_failure(session, row_id)
    session.flush()
    # Without its own row the count is at the limit again, so there is a wait; a good try may
    # have cleared rows meanwhile, hence the floor.
    return ip_retry_after_seconds(session, ip_hash, now=now, settings=settings) or 1
