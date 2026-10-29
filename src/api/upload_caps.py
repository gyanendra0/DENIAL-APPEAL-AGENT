"""The upload caps of individuals: `INDIVIDUAL_MONTHLY_UPLOADS` a UTC month per personal account,
and `INDIVIDUAL_DAILY_UPLOADS` a UTC day for all personal accounts together.

The monthly cap counts the account's own `uploads` while the account row is locked, so two
uploads of one account at the same moment cannot both pass it. The daily cap is kept in the
operational table `individual_upload_days` (one number a day, no account): one statement adds
one to the day's row only while it is under the cap, and Postgres locks that row while it does.
Both run in the upload's own transaction, so a refused or failed upload counts toward neither.
The shared account has no count cap.
"""

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.models import Account, IndividualUploadDay, Upload

logger = logging.getLogger(__name__)

MONTHLY_CAP_REACHED = "this account has used its uploads for this month; try again next month"
DAILY_CAP_REACHED = "today's uploads for personal accounts are used up; try again tomorrow"


class AccountGoneError(Exception):
    """The account was deleted while this upload waited on its row lock."""


class CapRefusal(StrEnum):
    """Which cap refused an upload."""

    MONTHLY = "monthly"
    DAILY = "daily"


def _day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(now: datetime) -> datetime:
    return _day_start(now).replace(day=1)


def _next_month_start(now: datetime) -> datetime:
    start = _month_start(now)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def retry_after_seconds(refusal: CapRefusal, now: datetime) -> int:
    """Whole seconds from `now` until the refused cap starts again (at least 1)."""
    if refusal is CapRefusal.MONTHLY:
        until = _next_month_start(now)
    else:
        until = _day_start(now) + timedelta(days=1)
    return max(1, int((until - now.astimezone(UTC)).total_seconds()))


def reserve_individual_upload(
    session: Session,
    account_id: int,
    *,
    now: datetime,
    monthly_limit: int,
    daily_limit: int,
) -> CapRefusal | None:
    """Count one upload of personal account `account_id` toward both caps and return None, or
    return the cap that refuses it (the daily one is then not counted). Does not commit.

    Locks the account row until the transaction ends; the caller writes the upload in the same
    transaction, so the next upload of this account counts it. Raises `AccountGoneError` when
    the account is gone once the lock is free ("Delete my account" holds the same lock).
    """
    locked = session.scalars(
        select(Account.id).where(Account.id == account_id).with_for_update()
    ).one_or_none()
    if locked is None:
        raise AccountGoneError
    this_month = session.scalar(
        select(func.count())
        .select_from(Upload)
        .where(Upload.account_id == account_id, Upload.created_at >= _month_start(now))
    )
    if (this_month or 0) >= monthly_limit:
        logger.info("upload refused: monthly cap of account %d", account_id)
        return CapRefusal.MONTHLY

    added = session.scalar(
        insert(IndividualUploadDay)
        .values(day=_day_start(now).date(), count=1, created_at=now, updated_at=now)
        .on_conflict_do_update(
            index_elements=["day"],
            set_={"count": IndividualUploadDay.count + 1, "updated_at": now},
            where=IndividualUploadDay.count < daily_limit,
        )
        .returning(IndividualUploadDay.count)
    )
    if added is None:
        logger.info("upload refused: daily cap of personal accounts")
        return CapRefusal.DAILY
    return None
