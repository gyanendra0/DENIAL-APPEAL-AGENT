"""The daily send cap: at most `MAIL_DAILY_LIMIT` emails a UTC day, every account together.

Counted in the operational table `mail_sends`, which holds only what each email was for and
when. Two requests at the same moment can both pass at the cap, so the app may send one or two
over it; the default (250) stays well under the free plan's 300 a day.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.db.models import EmailCodePurpose, MailSend

logger = logging.getLogger(__name__)

# Rows older than this count toward no day the cap still checks.
KEEP_FOR = timedelta(days=2)


def _day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def reserve_mail_send(
    session: Session, purpose: EmailCodePurpose, *, now: datetime, daily_limit: int
) -> bool:
    """Count one email of `purpose` toward today's cap and return True, or return False (and
    write nothing) when today already has `daily_limit` emails. Does not commit.

    Also deletes the rows older than two days.
    """
    session.execute(delete(MailSend).where(MailSend.created_at < now - KEEP_FOR))
    sent_today = session.scalar(
        select(func.count()).select_from(MailSend).where(MailSend.created_at >= _day_start(now))
    )
    if (sent_today or 0) >= daily_limit:
        logger.error("email not sent: the daily limit of %d is reached", daily_limit)
        return False
    session.add(MailSend(purpose=purpose, created_at=now, updated_at=now))
    session.flush()
    return True
