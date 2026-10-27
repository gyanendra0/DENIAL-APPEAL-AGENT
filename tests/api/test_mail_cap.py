"""The daily send cap counts every account's emails of the UTC day and forgets rows after two
days."""

import logging
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.mail_cap import reserve_mail_send
from src.db.models import EmailCodePurpose, MailSend

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
VERIFY = EmailCodePurpose.VERIFY_EMAIL


def _reserve(session: Session, at: datetime = NOW, limit: int = 2) -> bool:
    return reserve_mail_send(session, VERIFY, now=at, daily_limit=limit)


def _times(session: Session) -> list[datetime]:
    session.expire_all()
    return list(session.scalars(select(MailSend.created_at).order_by(MailSend.id)))


def test_counts_up_to_the_limit_then_refuses_without_writing(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    assert [_reserve(session), _reserve(session)] == [True, True]
    assert _reserve(session) is False
    assert _times(session) == [NOW, NOW]
    assert "daily limit of 2 is reached" in caplog.text


def test_the_row_holds_only_the_purpose_and_the_time(session: Session) -> None:
    _reserve(session)
    row = session.scalars(select(MailSend)).one()
    assert (row.purpose, row.created_at) == (VERIFY, NOW)


def test_a_new_utc_day_starts_a_new_count(session: Session) -> None:
    yesterday_late = datetime(2026, 10, 26, 23, 59, tzinfo=UTC)
    assert [_reserve(session, yesterday_late), _reserve(session, yesterday_late)] == [True, True]
    assert _reserve(session, datetime(2026, 10, 27, 0, 0, tzinfo=UTC)) is True


def test_the_day_is_the_utc_day_whatever_the_clock_zone(session: Session) -> None:
    _reserve(session, datetime(2026, 10, 27, 0, 30, tzinfo=UTC))
    _reserve(session, datetime(2026, 10, 27, 0, 30, tzinfo=UTC))
    # 22:00 on the 26th at UTC-4 is 02:00 on the 27th in UTC: the same, full day.
    late_evening_west = datetime(2026, 10, 26, 22, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert _reserve(session, late_evening_west) is False


def test_rows_older_than_two_days_are_deleted(session: Session) -> None:
    _reserve(session, NOW - timedelta(days=2, seconds=1))
    _reserve(session, NOW - timedelta(days=1))
    _reserve(session)
    assert _times(session) == [NOW - timedelta(days=1), NOW]
