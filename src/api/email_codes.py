"""Emailed 6-digit codes: made, stored as a keyed hash, checked, and limited.

A code is stored only as an HMAC-SHA256 over its purpose, its user and the code, with a key
derived from the token secret plus a label: a copy of the table without the secret reveals
nothing, and a code for one user or purpose never matches another. Only the user's newest code
of a purpose works, until it expires or has had too many wrong tries. Every query filters by
`account_id`.
"""

import hashlib
import hmac
import math
import secrets
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.orm import Session

from src.db.models import EmailCode, EmailCodePurpose
from src.db.query import for_account

# Mixed into the token secret, so the code key is never a key that signs tokens.
_KEY_LABEL = b"email-code"
CODE_DIGITS = 6
# One sentence for every code problem: wrong, expired, replaced or out of tries.
CODE_INVALID = "the code is wrong or has expired"
# The hourly cap counts the codes of this last stretch of time.
CAP_WINDOW = timedelta(hours=1)


def new_code() -> str:
    """A fresh random code: six digits, leading zeros kept."""
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def hash_code(purpose: EmailCodePurpose, user_id: int, code: str, *, secret: str) -> str:
    """The stored form of `code`: a lowercase hex HMAC-SHA256 over purpose, user and code."""
    key = hmac.new(secret.encode(), _KEY_LABEL, hashlib.sha256).digest()
    return hmac.new(key, f"{purpose.value}:{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _of_user(
    account_id: int, user_id: int, purpose: EmailCodePurpose
) -> tuple[ColumnElement[bool], ...]:
    return (
        EmailCode.account_id == account_id,
        EmailCode.user_id == user_id,
        EmailCode.purpose == purpose,
    )


def add_code(
    session: Session,
    *,
    account_id: int,
    user_id: int,
    purpose: EmailCodePurpose,
    code: str,
    secret: str,
    ttl_minutes: int,
    now: datetime,
) -> None:
    """Store `code` as the user's newest code of `purpose`, valid `ttl_minutes` after `now`.

    Also deletes the user's rows of that purpose that are expired and older than the cap window
    (no longer able to open anything or to count toward the cap). Does not commit.
    """
    session.execute(
        delete(EmailCode).where(
            *_of_user(account_id, user_id, purpose),
            EmailCode.expires_at <= now,
            EmailCode.created_at <= now - CAP_WINDOW,
        )
    )
    session.add(
        EmailCode(
            account_id=account_id,
            user_id=user_id,
            purpose=purpose,
            code_hash=hash_code(purpose, user_id, code, secret=secret),
            expires_at=now + timedelta(minutes=ttl_minutes),
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


def resend_wait_seconds(
    session: Session,
    *,
    account_id: int,
    user_id: int,
    purpose: EmailCodePurpose,
    now: datetime,
    gap_seconds: int,
    per_hour: int,
) -> int | None:
    """Seconds until the user may get a new code of `purpose`, or None if allowed now.

    A new code needs `gap_seconds` since the last one and fewer than `per_hour` inside the cap
    window. Lock the user row first, or two requests at the same time can both pass.
    """
    recent = session.scalars(
        select(EmailCode.created_at)
        .where(*_of_user(account_id, user_id, purpose), EmailCode.created_at > now - CAP_WINDOW)
        .order_by(EmailCode.created_at.desc())
    ).all()
    waits: list[float] = []
    if recent:
        waits.append((recent[0] + timedelta(seconds=gap_seconds) - now).total_seconds())
    if len(recent) >= per_hour:
        # The oldest of the newest `per_hour` codes has to leave the window first.
        waits.append((recent[per_hour - 1] + CAP_WINDOW - now).total_seconds())
    wait = max(waits, default=0.0)
    return max(1, math.ceil(wait)) if wait > 0 else None


def check_code(
    session: Session,
    *,
    account_id: int,
    user_id: int,
    purpose: EmailCodePurpose,
    code: str,
    secret: str,
    now: datetime,
    max_wrong_tries: int,
) -> bool:
    """True if `code` is the user's newest unexpired code of `purpose`.

    A wrong code counts one wrong try against that newest code; at `max_wrong_tries` the code
    is deleted. Lock the user row first, so tries at the same time are all counted. Does not
    commit, and does not delete a right code (the caller does, with `delete_codes`).
    """
    newest = session.scalars(
        for_account(EmailCode, account_id)
        .where(EmailCode.user_id == user_id, EmailCode.purpose == purpose)
        .order_by(EmailCode.id.desc())
        .limit(1)
    ).one_or_none()
    if newest is None or newest.expires_at <= now:
        return False
    if secrets.compare_digest(newest.code_hash, hash_code(purpose, user_id, code, secret=secret)):
        return True
    newest.wrong_tries += 1
    if newest.wrong_tries >= max_wrong_tries:
        session.delete(newest)
    session.flush()
    return False


def delete_codes(
    session: Session, *, account_id: int, user_id: int, purpose: EmailCodePurpose
) -> None:
    """Delete every code of `purpose` the user has (used, or no longer needed)."""
    session.execute(delete(EmailCode).where(*_of_user(account_id, user_id, purpose)))
