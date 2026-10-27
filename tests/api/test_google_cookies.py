"""The Google sign-in cookies read back, last ten minutes, and refuse every forgery and mix-up."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from src.api.google_cookies import (
    COOKIE_TTL,
    FLOW_TYPE,
    GoogleFlow,
    InvalidGoogleCookieError,
    PendingSignup,
    _key,
    issue_flow_cookie,
    issue_signup_cookie,
    new_flow,
    read_flow_cookie,
    read_signup_cookie,
)
from src.api.tokens import issue_token
from src.api.verify_cookie import issue_verify_cookie

SECRET = "a-made-up-test-secret-of-forty-characters"  # noqa: S105  (made up)
OTHER_SECRET = "another-made-up-secret-of-forty-characters"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
FLOW = GoogleFlow(state="made-up-state", nonce="made-up-nonce", verifier="made-up-verifier")
PENDING = PendingSignup(subject="100000000000000000001", email="ann@gmail.com")


def _flow_value(now: datetime = NOW, secret: str = SECRET) -> str:
    return issue_flow_cookie(FLOW, secret=secret, now=now)


def _forged(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, _key(SECRET), algorithm="HS256")


def test_a_flow_cookie_reads_back_its_state_nonce_and_verifier() -> None:
    assert read_flow_cookie(_flow_value(), secret=SECRET, now=NOW) == FLOW


def test_a_signup_cookie_reads_back_the_google_account_and_email() -> None:
    value = issue_signup_cookie(PENDING, secret=SECRET, now=NOW)
    assert read_signup_cookie(value, secret=SECRET, now=NOW) == PENDING


def test_a_cookie_lasts_ten_minutes() -> None:
    assert COOKIE_TTL.total_seconds() == 600
    just_before = NOW + COOKIE_TTL - timedelta(seconds=1)
    assert read_flow_cookie(_flow_value(), secret=SECRET, now=just_before) == FLOW
    with pytest.raises(InvalidGoogleCookieError):
        read_flow_cookie(_flow_value(), secret=SECRET, now=NOW + COOKIE_TTL)


def test_a_cookie_signed_with_another_secret_is_refused() -> None:
    with pytest.raises(InvalidGoogleCookieError):
        read_flow_cookie(_flow_value(secret=OTHER_SECRET), secret=SECRET, now=NOW)


def test_a_flow_cookie_is_not_a_signup_cookie_and_back() -> None:
    signup_value = issue_signup_cookie(PENDING, secret=SECRET, now=NOW)
    with pytest.raises(InvalidGoogleCookieError):
        read_signup_cookie(_flow_value(), secret=SECRET, now=NOW)
    with pytest.raises(InvalidGoogleCookieError):
        read_flow_cookie(signup_value, secret=SECRET, now=NOW)


def test_a_login_token_or_a_verify_cookie_is_not_a_google_cookie() -> None:
    login = issue_token(
        user_id=1, account_id=1, token_version=0, secret=SECRET, ttl_minutes=60, now=NOW
    )
    verify = issue_verify_cookie(user_id=1, account_id=1, secret=SECRET, now=NOW)
    for value in (login, verify):
        with pytest.raises(InvalidGoogleCookieError):
            read_flow_cookie(value, secret=SECRET, now=NOW)
        with pytest.raises(InvalidGoogleCookieError):
            read_signup_cookie(value, secret=SECRET, now=NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"state": None},
        {"state": ""},
        {"nonce": 7},
        {"typ": None},
        {"exp": "later"},
    ],
)
def test_a_flow_cookie_with_a_missing_or_bad_claim_is_refused(change: dict[str, Any]) -> None:
    payload: dict[str, Any] = {
        **FLOW.model_dump(),
        "typ": FLOW_TYPE,
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + COOKIE_TTL).timestamp()),
    }
    payload.update(change)
    forged = _forged({key: value for key, value in payload.items() if value is not None})
    with pytest.raises(InvalidGoogleCookieError):
        read_flow_cookie(forged, secret=SECRET, now=NOW)


def test_a_damaged_cookie_is_refused_without_repeating_it() -> None:
    with pytest.raises(InvalidGoogleCookieError) as caught:
        read_flow_cookie("not-a-token", secret=SECRET, now=NOW)
    assert "not-a-token" not in str(caught.value)


def test_a_cookie_needs_a_timezone_aware_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        issue_flow_cookie(FLOW, secret=SECRET, now=NOW.replace(tzinfo=None))


def test_a_new_flow_is_random_and_its_verifier_has_a_pkce_length() -> None:
    first, second = new_flow(), new_flow()
    assert len({first.state, first.nonce, first.verifier, second.state}) == 4
    assert 43 <= len(first.verifier) <= 128
