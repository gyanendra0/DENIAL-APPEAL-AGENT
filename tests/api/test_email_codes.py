"""Emailed codes: six digits, stored as a keyed hash, only the newest unexpired one works, wrong
tries kill it, and the resend limits count a user's recent codes."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api import email_codes
from src.api.email_codes import (
    add_code,
    check_code,
    delete_codes,
    hash_code,
    new_code,
    resend_wait_seconds,
)
from src.api.signup import create_account_with_admin
from src.db.models import EmailCode, EmailCodePurpose, User

SECRET = "a-made-up-code-test-secret-of-forty-chars"  # noqa: S105  (made up)
PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
VERIFY = EmailCodePurpose.VERIFY_EMAIL
RESET = EmailCodePurpose.PASSWORD_RESET


def _user(session: Session, email: str = "ann@x.org", name: str = "One") -> User:
    return create_account_with_admin(
        session, account_name=name, email=email, password=PASSWORD, verified_at=None
    )


def _add(
    session: Session,
    user: User,
    code: str = "123456",
    *,
    purpose: EmailCodePurpose = VERIFY,
    at: datetime = NOW,
) -> None:
    add_code(
        session,
        account_id=user.account_id,
        user_id=user.id,
        purpose=purpose,
        code=code,
        secret=SECRET,
        ttl_minutes=10,
        now=at,
    )


def _check(
    session: Session,
    user: User,
    code: str = "123456",
    *,
    purpose: EmailCodePurpose = VERIFY,
    at: datetime = NOW,
    account_id: int | None = None,
) -> bool:
    return check_code(
        session,
        account_id=user.account_id if account_id is None else account_id,
        user_id=user.id,
        purpose=purpose,
        code=code,
        secret=SECRET,
        now=at,
        max_wrong_tries=5,
    )


def _wait(session: Session, user: User, at: datetime = NOW) -> int | None:
    return resend_wait_seconds(
        session,
        account_id=user.account_id,
        user_id=user.id,
        purpose=VERIFY,
        now=at,
        gap_seconds=60,
        per_hour=5,
    )


def _rows(session: Session) -> list[EmailCode]:
    session.expire_all()
    return list(session.scalars(select(EmailCode).order_by(EmailCode.id)))


class _FixedSecrets:
    """Stands in for `secrets`; its random number is always 42."""

    def randbelow(self, _: int) -> int:
        return 42


def test_a_new_code_is_six_digits_with_leading_zeros_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert len(new_code()) == 6 and new_code().isdecimal()
    monkeypatch.setattr(email_codes, "secrets", _FixedSecrets())
    assert new_code() == "000042"


def test_the_hash_depends_on_purpose_user_code_and_secret() -> None:
    base = hash_code(VERIFY, 1, "123456", secret=SECRET)
    assert len(base) == 64 and base == base.lower()
    others = {
        hash_code(RESET, 1, "123456", secret=SECRET),
        hash_code(VERIFY, 2, "123456", secret=SECRET),
        hash_code(VERIFY, 1, "123457", secret=SECRET),
        hash_code(VERIFY, 1, "123456", secret=SECRET + "x"),
    }
    assert base not in others and len(others) == 4


def test_the_code_is_stored_only_as_its_hash_with_its_expiry(session: Session) -> None:
    user = _user(session)
    _add(session, user, "654321")
    (row,) = _rows(session)
    assert row.code_hash == hash_code(VERIFY, user.id, "654321", secret=SECRET)
    assert "654321" not in row.code_hash
    assert (row.expires_at, row.wrong_tries, row.purpose) == (
        NOW + timedelta(minutes=10),
        0,
        VERIFY,
    )


def test_the_right_code_passes_and_is_not_deleted_by_the_check(session: Session) -> None:
    user = _user(session)
    _add(session, user)
    assert _check(session, user) is True
    assert len(_rows(session)) == 1


def test_a_code_of_another_purpose_or_account_does_not_pass(session: Session) -> None:
    user = _user(session)
    other = _user(session, "bob@x.org", "Two")
    _add(session, user)
    assert _check(session, user, purpose=RESET) is False
    assert _check(session, user, account_id=other.account_id) is False


def test_an_expired_code_does_not_pass(session: Session) -> None:
    user = _user(session)
    _add(session, user)
    assert _check(session, user, at=NOW + timedelta(minutes=10)) is False


def test_only_the_newest_code_passes(session: Session) -> None:
    user = _user(session)
    _add(session, user, "111111")
    _add(session, user, "222222", at=NOW + timedelta(minutes=1))
    assert _check(session, user, "111111", at=NOW + timedelta(minutes=2)) is False
    assert _check(session, user, "222222", at=NOW + timedelta(minutes=2)) is True


def test_a_wrong_code_counts_and_the_fifth_wrong_try_kills_the_code(session: Session) -> None:
    user = _user(session)
    _add(session, user)
    for tries in range(1, 6):
        assert _check(session, user, "000000") is False
        assert _rows(session)[0].wrong_tries == tries
    assert _check(session, user) is False
    # A dead code is kept, and further tries no longer count.
    assert [row.wrong_tries for row in _rows(session)] == [5]


def test_an_older_code_does_not_pass_once_the_newer_one_is_dead(session: Session) -> None:
    user = _user(session)
    _add(session, user, "111111")
    _add(session, user, "222222", at=NOW + timedelta(minutes=1))
    for _ in range(5):
        assert _check(session, user, "000000", at=NOW + timedelta(minutes=2)) is False
    assert _check(session, user, "111111", at=NOW + timedelta(minutes=2)) is False
    assert _check(session, user, "222222", at=NOW + timedelta(minutes=2)) is False


def test_adding_a_code_deletes_only_rows_expired_and_older_than_an_hour(session: Session) -> None:
    user = _user(session)
    _add(session, user, "111111", at=NOW - timedelta(minutes=61))
    _add(session, user, "222222", at=NOW - timedelta(minutes=30))
    _add(session, user, "333333", purpose=RESET, at=NOW - timedelta(minutes=61))
    _add(session, user, "444444")
    kept = [(row.purpose, row.created_at) for row in _rows(session)]
    assert kept == [
        (VERIFY, NOW - timedelta(minutes=30)),
        (RESET, NOW - timedelta(minutes=61)),
        (VERIFY, NOW),
    ]


def test_delete_codes_removes_only_that_users_codes_of_that_purpose(session: Session) -> None:
    user = _user(session)
    other = _user(session, "bob@x.org", "Two")
    _add(session, user)
    _add(session, user, purpose=RESET)
    _add(session, other)
    delete_codes(session, account_id=user.account_id, user_id=user.id, purpose=VERIFY)
    assert [(row.user_id, row.purpose) for row in _rows(session)] == [
        (user.id, RESET),
        (other.id, VERIFY),
    ]


def test_no_wait_without_recent_codes(session: Session) -> None:
    user = _user(session)
    assert _wait(session, user) is None
    _add(session, user, at=NOW - timedelta(minutes=61))
    assert _wait(session, user) is None


def test_a_new_code_waits_out_the_gap_since_the_last_one(session: Session) -> None:
    user = _user(session)
    _add(session, user, at=NOW - timedelta(seconds=45))
    assert _wait(session, user) == 15
    assert _wait(session, user, NOW + timedelta(seconds=15)) is None


def test_the_sixth_code_in_an_hour_waits_until_the_oldest_leaves_the_hour(
    session: Session,
) -> None:
    user = _user(session)
    for minutes in (50, 40, 30, 20, 10):
        _add(session, user, at=NOW - timedelta(minutes=minutes))
    assert _wait(session, user) == 10 * 60
    assert _wait(session, user, NOW + timedelta(minutes=10)) is None


def test_a_dead_code_still_counts_toward_the_hourly_cap(session: Session) -> None:
    user = _user(session)
    for minutes in (50, 40, 30, 20, 10):
        _add(session, user, at=NOW - timedelta(minutes=minutes))
        for _ in range(5):
            _check(session, user, "000000", at=NOW - timedelta(minutes=minutes))
    assert len(_rows(session)) == 5
    assert _wait(session, user) == 10 * 60
    # Ten minutes on, the oldest has left the hour; a sixth dead code fills the cap again.
    _add(session, user, at=NOW + timedelta(minutes=10))
    for _ in range(5):
        _check(session, user, "000000", at=NOW + timedelta(minutes=10))
    assert _wait(session, user, NOW + timedelta(minutes=10, seconds=30)) == 9 * 60 + 30
