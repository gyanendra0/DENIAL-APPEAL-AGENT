"""Reset tokens: the signed envelope reads back, refuses every forgery, and only the user's newest
unexpired row works."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.reset_tokens import (
    InvalidResetTokenError,
    IssuedResetToken,
    ResetClaims,
    add_reset_token,
    count_recent_reset_tokens,
    delete_reset_tokens,
    delete_stale_reset_tokens,
    hash_token_id,
    issue_reset_token,
    read_reset_token,
    usable_reset_token,
)
from src.api.signup import create_account_with_admin
from src.api.tokens import issue_token
from src.db.models import PasswordResetToken, User

# The users made here count as having confirmed their email.
VERIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)

SECRET = "a-made-up-reset-test-secret-of-forty-chars"  # noqa: S105  (made up)
OTHER_SECRET = "another-made-up-reset-secret-of-forty-chr"  # noqa: S105  (made up)
PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
RANDOM_PART = "made-up-random-part"  # noqa: S105  (made up)


def _issue(now: datetime = NOW, ttl_minutes: int = 30) -> IssuedResetToken:
    return issue_reset_token(
        user_id=7, account_id=3, secret=SECRET, ttl_minutes=ttl_minutes, now=now
    )


def _reset_key(secret: str = SECRET) -> bytes:
    return hmac.new(secret.encode(), b"password-reset", hashlib.sha256).digest()


def _forged(**changes: Any) -> str:
    payload: dict[str, Any] = {
        "sub": "7",
        "acct": 3,
        "jti": RANDOM_PART,
        "typ": "pwreset",
        "iat": NOW,
        "exp": NOW + timedelta(minutes=5),
    }
    payload.update(changes)
    payload = {key: value for key, value in payload.items() if value is not None}
    return jwt.encode(payload, _reset_key(), algorithm="HS256")


# --- the envelope ---


def test_a_token_reads_back_as_its_user_account_and_random_part() -> None:
    issued = _issue()
    claims = read_reset_token(issued.token, secret=SECRET, now=NOW)
    assert (claims.user_id, claims.account_id) == (7, 3)
    assert issued.token_hash == hash_token_id(claims.token_id)
    assert issued.expires_at == NOW + timedelta(minutes=30)


def test_the_stored_hash_is_lowercase_hex_and_never_the_token() -> None:
    issued = _issue()
    assert len(issued.token_hash) == 64 and set(issued.token_hash) <= set("0123456789abcdef")
    assert issued.token_hash not in issued.token


def test_two_tokens_for_the_same_user_differ() -> None:
    assert _issue().token != _issue().token


def test_issue_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _issue(now=datetime(2026, 10, 26, 12, 0))  # noqa: DTZ001  (naive on purpose)


def test_a_token_is_refused_once_its_time_is_up() -> None:
    issued = _issue()
    read_reset_token(issued.token, secret=SECRET, now=NOW + timedelta(minutes=29))
    with pytest.raises(InvalidResetTokenError):
        read_reset_token(issued.token, secret=SECRET, now=NOW + timedelta(minutes=30))


def test_a_token_signed_with_another_secret_is_refused() -> None:
    with pytest.raises(InvalidResetTokenError):
        read_reset_token(_issue().token, secret=OTHER_SECRET, now=NOW)


def test_a_login_token_is_never_a_reset_token() -> None:
    login = issue_token(
        user_id=7, account_id=3, token_version=0, secret=SECRET, ttl_minutes=60, now=NOW
    )
    with pytest.raises(InvalidResetTokenError):
        read_reset_token(login, secret=SECRET, now=NOW)


def test_a_tampered_token_is_refused_without_repeating_it() -> None:
    token = _issue().token
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    with pytest.raises(InvalidResetTokenError) as caught:
        read_reset_token(tampered, secret=SECRET, now=NOW)
    assert tampered not in str(caught.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"typ": "login"},
        {"typ": None},
        {"sub": 7},
        {"sub": "seven"},
        {"acct": "3"},
        {"acct": True},
        {"jti": ""},
        {"jti": 5},
        {"jti": None},
        {"exp": None},
    ],
    ids=[
        "other-type",
        "no-type",
        "number-sub",
        "word-sub",
        "text-account",
        "bool-account",
        "empty-random-part",
        "number-random-part",
        "no-random-part",
        "no-expiry",
    ],
)
def test_a_signed_token_with_a_wrong_claim_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(InvalidResetTokenError):
        read_reset_token(_forged(**changes), secret=SECRET, now=NOW)


def test_a_token_with_every_claim_right_reads() -> None:
    # The base of the cases above, so each of them fails for its one change only.
    claims = read_reset_token(_forged(), secret=SECRET, now=NOW)
    assert claims == ResetClaims(user_id=7, account_id=3, token_id=RANDOM_PART)


# --- the rows ---


def _user(session: Session, email: str = "ann@x.org", name: str = "One") -> User:
    return create_account_with_admin(
        session, account_name=name, email=email, password=PASSWORD, verified_at=VERIFIED_AT
    )


def _add(session: Session, user: User, at: datetime, ttl_minutes: int = 30) -> ResetClaims:
    issued = issue_reset_token(
        user_id=user.id, account_id=user.account_id, secret=SECRET, ttl_minutes=ttl_minutes, now=at
    )
    add_reset_token(session, account_id=user.account_id, user_id=user.id, issued=issued, now=at)
    session.flush()
    return read_reset_token(issued.token, secret=SECRET, now=at)


def _count(session: Session, user: User, now: datetime = NOW) -> int:
    return count_recent_reset_tokens(session, account_id=user.account_id, user_id=user.id, now=now)


def test_a_new_row_works_until_it_expires(session: Session) -> None:
    user = _user(session)
    claims = _add(session, user, NOW)
    assert usable_reset_token(session, claims, now=NOW + timedelta(minutes=29))
    assert not usable_reset_token(session, claims, now=NOW + timedelta(minutes=30))


def test_a_newer_row_makes_the_older_one_useless(session: Session) -> None:
    user = _user(session)
    older = _add(session, user, NOW)
    newer = _add(session, user, NOW + timedelta(minutes=1))
    later = NOW + timedelta(minutes=2)
    assert not usable_reset_token(session, older, now=later)
    assert usable_reset_token(session, newer, now=later)


def test_a_token_with_no_row_does_not_work(session: Session) -> None:
    user = _user(session)
    claims = ResetClaims(user_id=user.id, account_id=user.account_id, token_id=RANDOM_PART)
    assert not usable_reset_token(session, claims, now=NOW)


def test_a_row_of_another_user_does_not_open_this_user(session: Session) -> None:
    ann, bob = _user(session), _user(session, "bob@x.org", "Two")
    claims = _add(session, ann, NOW)
    as_bob = ResetClaims(user_id=bob.id, account_id=bob.account_id, token_id=claims.token_id)
    assert not usable_reset_token(session, as_bob, now=NOW)


def test_the_count_covers_only_this_user_in_the_last_hour(session: Session) -> None:
    ann, bob = _user(session), _user(session, "bob@x.org", "Two")
    _add(session, ann, NOW - timedelta(minutes=61))
    _add(session, ann, NOW - timedelta(minutes=59))
    _add(session, ann, NOW)
    _add(session, bob, NOW)
    assert _count(session, ann) == 2
    assert _count(session, bob) == 1


def test_stale_rows_go_but_rows_still_counted_stay(session: Session) -> None:
    user = _user(session)
    _add(session, user, NOW - timedelta(minutes=90))  # expired and out of the hour: goes
    _add(session, user, NOW - timedelta(minutes=45))  # expired but still counted: stays
    _add(session, user, NOW)
    delete_stale_reset_tokens(session, account_id=user.account_id, user_id=user.id, now=NOW)
    session.flush()
    created = sorted(
        session.scalars(
            select(PasswordResetToken.created_at).where(PasswordResetToken.user_id == user.id)
        ).all()
    )
    assert created == [NOW - timedelta(minutes=45), NOW]


def test_deleting_a_users_rows_leaves_other_users_rows(session: Session) -> None:
    ann, bob = _user(session), _user(session, "bob@x.org", "Two")
    _add(session, ann, NOW)
    _add(session, ann, NOW)
    _add(session, bob, NOW)
    delete_reset_tokens(session, account_id=ann.account_id, user_id=ann.id)
    session.flush()
    assert (_count(session, ann), _count(session, bob)) == (0, 1)
