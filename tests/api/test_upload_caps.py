"""The individuals' upload caps: per personal account a UTC month, all of them together a UTC day.

Every account and letter here is made up.
"""

import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.orm import Session

from src.api.upload_caps import CapRefusal, reserve_individual_upload, retry_after_seconds
from src.db.models import Account, AccountKind, IndividualUploadDay, Job, Upload, User
from src.jobs.store import create_upload_job
from tests.api.conftest import waits_on_a_lock
from tests.jobs.helpers import Member, new_upload

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
LAST_MONTH = datetime(2026, 9, 30, 23, 59, tzinfo=UTC)
NAME_PREFIX = "made-up-personal-cap-"


def _individual(session: Session, name: str) -> Member:
    account = Account(name=NAME_PREFIX + name, kind=AccountKind.PERSONAL)
    session.add(account)
    session.flush()
    user = User(account_id=account.id, email=f"{name}@elsewhere.test")
    session.add(user)
    session.flush()
    return Member(account_id=account.id, user_id=user.id)


def _upload_at(session: Session, member: Member, at: datetime) -> None:
    job_id = create_upload_job(session, member.account_id, new_upload(member))
    upload_id = session.scalars(
        select(Job.upload_id).where(Job.account_id == member.account_id, Job.id == job_id)
    ).one()
    session.execute(
        update(Upload)
        .where(Upload.account_id == member.account_id, Upload.id == upload_id)
        .values(created_at=at)
    )


def _reserve(
    session: Session, member: Member, *, monthly: int = 5, daily: int = 10, now: datetime = NOW
) -> CapRefusal | None:
    return reserve_individual_upload(
        session, member.account_id, now=now, monthly_limit=monthly, daily_limit=daily
    )


def _day_count(session: Session, day: datetime = NOW) -> int | None:
    return session.scalar(
        select(IndividualUploadDay.count).where(IndividualUploadDay.day == day.date())
    )


def test_an_individual_under_both_caps_passes_and_counts_toward_the_day(
    session: Session,
) -> None:
    ann = _individual(session, "ann")
    assert _reserve(session, ann) is None
    assert _reserve(session, ann) is None
    assert _day_count(session) == 2


def test_the_monthly_cap_counts_the_accounts_uploads_this_month_only(session: Session) -> None:
    ann = _individual(session, "ann")
    bob = _individual(session, "bob")
    _upload_at(session, ann, LAST_MONTH)
    _upload_at(session, bob, NOW)
    _upload_at(session, ann, NOW)

    # Ann has one upload this month (last month's and Bob's do not count).
    assert _reserve(session, ann, monthly=2) is None
    _upload_at(session, ann, NOW)
    assert _reserve(session, ann, monthly=2) is CapRefusal.MONTHLY
    # A refused upload is not counted toward the day.
    assert _day_count(session) == 1


def test_the_daily_cap_counts_every_individual_together(session: Session) -> None:
    ann = _individual(session, "ann")
    bob = _individual(session, "bob")
    assert _reserve(session, ann, daily=2) is None
    assert _reserve(session, bob, daily=2) is None

    assert _reserve(session, ann, daily=2) is CapRefusal.DAILY
    assert _day_count(session) == 2


def test_a_new_utc_day_starts_a_new_count(session: Session) -> None:
    ann = _individual(session, "ann")
    late = datetime(2026, 10, 29, 23, 59, tzinfo=UTC)
    early = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)
    assert _reserve(session, ann, daily=1, now=late) is None
    assert _reserve(session, ann, daily=1, now=late) is CapRefusal.DAILY
    assert _reserve(session, ann, daily=1, now=early) is None
    assert (_day_count(session, late), _day_count(session, early)) == (1, 1)


@pytest.mark.parametrize(
    ("refusal", "now", "seconds"),
    [
        (CapRefusal.DAILY, NOW, 12 * 3600),
        (CapRefusal.DAILY, datetime(2026, 10, 29, 23, 59, 59, 900_000, tzinfo=UTC), 1),
        (CapRefusal.MONTHLY, NOW, (2 * 24 + 12) * 3600),
        (CapRefusal.MONTHLY, datetime(2026, 12, 31, 12, 0, tzinfo=UTC), 12 * 3600),
    ],
)
def test_retry_after_is_the_seconds_until_the_cap_starts_again(
    refusal: CapRefusal, now: datetime, seconds: int
) -> None:
    assert retry_after_seconds(refusal, now) == seconds


def _cleanup(engine: Engine) -> None:
    with Session(engine) as cleanup:
        cleanup.execute(delete(Account).where(Account.name.startswith(NAME_PREFIX)))
        cleanup.execute(delete(IndividualUploadDay).where(IndividualUploadDay.day == NOW.date()))
        cleanup.commit()


def _race(
    engine: Engine, first_member: str, second_member: str, *, monthly: int, daily: int
) -> list[object]:
    """Reserve and upload in one connection and, while it is not committed, reserve in a second;
    return what the second got once the first committed."""
    found: list[object] = []
    with Session(engine) as setup:
        first_of = _individual(setup, first_member)
        second_of = first_of if second_member == first_member else _individual(setup, second_member)
        setup.commit()
    with Session(engine) as first, Session(engine) as second:
        assert _reserve(first, first_of, monthly=monthly, daily=daily) is None
        create_upload_job(first, first_of.account_id, new_upload(first_of))
        second_pid = second.scalar(text("SELECT pg_backend_pid()"))
        assert second_pid is not None
        worker = threading.Thread(
            target=lambda: found.append(_reserve(second, second_of, monthly=monthly, daily=daily))
        )
        worker.start()
        assert waits_on_a_lock(engine, second_pid)
        first.commit()
        worker.join(timeout=5)
        second.rollback()
    return found


def test_two_uploads_of_one_account_at_the_same_moment_cannot_both_pass_the_monthly_cap(
    engine: Engine,
) -> None:
    """The second waits on the account row, then counts the first one's upload."""
    try:
        assert _race(engine, "ann", "ann", monthly=1, daily=10) == [CapRefusal.MONTHLY]
    finally:
        _cleanup(engine)


def test_two_individuals_at_the_same_moment_cannot_both_take_the_last_place_of_the_day(
    engine: Engine,
) -> None:
    """The second waits on the day's row, then finds it at the cap."""
    try:
        assert _race(engine, "ann", "bob", monthly=5, daily=1) == [CapRefusal.DAILY]
        with Session(engine) as check:
            assert _day_count(check) == 1
    finally:
        _cleanup(engine)
