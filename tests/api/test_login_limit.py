"""The login limit hashes the email and the client IP, counts failures in a sliding window, and
says how long to wait."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.login_limit import (
    LoginKeys,
    clear_email_failures,
    login_keys,
    record_failure,
    retry_after_seconds,
)
from src.config.settings import ApiSettings
from src.db.models import LoginFailure, LoginKeyKind

SECRET = "a-made-up-login-limit-secret-of-40-chars"  # noqa: S105  (made up)
OTHER_SECRET = "another-made-up-secret-of-forty-chars!!"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
WINDOW = 15


def _settings(per_email: int = 5, per_ip: int = 20) -> ApiSettings:
    return ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(SECRET),
        login_limit_window_minutes=WINDOW,
        login_limit_per_email=per_email,
        login_limit_per_ip=per_ip,
        _env_file=None,
    )


def _keys(email: str = "ann@x.org", host: str | None = "203.0.113.7") -> LoginKeys:
    return login_keys(email, host, SECRET)


def _fail(session: Session, keys: LoginKeys, at: datetime, times: int = 1) -> None:
    for _ in range(times):
        record_failure(session, keys, now=at, window_minutes=WINDOW)
    session.flush()


def _rows(session: Session) -> list[tuple[LoginKeyKind, str]]:
    return [(row.key_kind, row.key_hash) for row in session.scalars(select(LoginFailure)).all()]


def test_keys_are_hex_hmacs_that_never_equal_the_value() -> None:
    keys = _keys()
    for value in (keys.email_hash, keys.ip_hash):
        assert len(value) == 64 and set(value) <= set("0123456789abcdef")
    assert "ann@x.org" not in keys.email_hash and "203.0.113.7" not in keys.ip_hash
    assert keys.email_hash != keys.ip_hash


def test_the_same_value_gives_the_same_key_and_another_secret_another_key() -> None:
    assert _keys() == _keys()
    assert login_keys("ann@x.org", "203.0.113.7", OTHER_SECRET) != _keys()
    assert _keys(email="bob@x.org").email_hash != _keys().email_hash


def test_ipv4_is_counted_per_address() -> None:
    assert _keys(host="203.0.113.7").ip_hash != _keys(host="203.0.113.8").ip_hash


def test_ipv6_is_counted_by_its_64_network() -> None:
    same_a = _keys(host="2001:db8:1:2::1").ip_hash
    same_b = _keys(host="2001:db8:1:2:ffff:ffff:ffff:ffff").ip_hash
    other = _keys(host="2001:db8:1:3::1").ip_hash
    assert same_a == same_b
    assert same_a != other


def test_an_ipv4_address_in_ipv6_form_counts_as_the_ipv4_address() -> None:
    assert _keys(host="::ffff:203.0.113.7").ip_hash == _keys(host="203.0.113.7").ip_hash


@pytest.mark.parametrize("host", ["testclient", None])
def test_a_host_that_is_not_an_ip_still_gets_a_key(host: str | None) -> None:
    keys = _keys(host=host)
    assert len(keys.ip_hash) == 64
    assert keys.ip_hash != _keys(host="203.0.113.7").ip_hash


def test_below_the_limit_the_login_is_allowed(session: Session) -> None:
    _fail(session, _keys(), NOW, times=4)
    assert retry_after_seconds(session, _keys(), now=NOW, settings=_settings()) is None


def test_at_the_email_limit_the_wait_runs_until_the_oldest_failure_leaves(
    session: Session,
) -> None:
    _fail(session, _keys(), NOW - timedelta(minutes=10))
    _fail(session, _keys(), NOW - timedelta(minutes=1), times=4)
    wait = retry_after_seconds(session, _keys(), now=NOW, settings=_settings())
    assert wait == 5 * 60


def test_a_part_second_wait_is_rounded_up_to_at_least_one(session: Session) -> None:
    _fail(session, _keys(), NOW - timedelta(minutes=WINDOW) + timedelta(milliseconds=200), 5)
    assert retry_after_seconds(session, _keys(), now=NOW, settings=_settings()) == 1


def test_once_the_window_has_passed_the_login_is_allowed_again(session: Session) -> None:
    _fail(session, _keys(), NOW, times=5)
    later = NOW + timedelta(minutes=WINDOW)
    assert retry_after_seconds(session, _keys(), now=later, settings=_settings()) is None


def test_the_ip_limit_counts_failures_over_many_emails(session: Session) -> None:
    for number in range(3):
        _fail(session, _keys(email=f"user{number}@x.org"), NOW)
    settings = _settings(per_ip=3)
    assert retry_after_seconds(session, _keys(email="fresh@x.org"), now=NOW, settings=settings)
    elsewhere = _keys(email="fresh@x.org", host="198.51.100.1")
    assert retry_after_seconds(session, elsewhere, now=NOW, settings=settings) is None


def test_when_both_keys_are_at_the_limit_the_longer_wait_wins(session: Session) -> None:
    _fail(session, _keys(), NOW - timedelta(minutes=12))
    _fail(session, _keys(email="other@x.org"), NOW - timedelta(minutes=5))
    _fail(session, _keys(), NOW - timedelta(minutes=1))
    settings = _settings(per_email=2, per_ip=2)
    # Email: failures at -12 and -1 min, free in 3 min. IP: the newest two are at -5 and -1 min,
    # free in 10 min.
    assert retry_after_seconds(session, _keys(), now=NOW, settings=settings) == 10 * 60


def test_a_failure_writes_one_row_per_key(session: Session) -> None:
    keys = _keys()
    _fail(session, keys, NOW)
    assert sorted(_rows(session)) == sorted(
        [(LoginKeyKind.EMAIL, keys.email_hash), (LoginKeyKind.IP, keys.ip_hash)]
    )
    stored = session.scalars(select(LoginFailure)).first()
    assert stored is not None and stored.created_at == NOW


def test_a_new_failure_deletes_rows_older_than_the_window(session: Session) -> None:
    _fail(session, _keys(email="old@x.org", host="198.51.100.1"), NOW - timedelta(minutes=16))
    _fail(session, _keys(), NOW)
    assert len(_rows(session)) == 2
    assert all(row.created_at == NOW for row in session.scalars(select(LoginFailure)))


def test_clearing_the_email_keeps_the_ip_failures(session: Session) -> None:
    keys = _keys()
    _fail(session, keys, NOW, times=2)
    _fail(session, _keys(email="other@x.org", host="198.51.100.1"), NOW)
    clear_email_failures(session, keys)
    session.flush()
    remaining = _rows(session)
    assert (LoginKeyKind.EMAIL, keys.email_hash) not in remaining
    assert remaining.count((LoginKeyKind.IP, keys.ip_hash)) == 2
    assert len(remaining) == 4
