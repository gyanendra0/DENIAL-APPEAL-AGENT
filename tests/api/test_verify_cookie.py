"""The `email_verify` cookie reads back, refuses every forgery and expiry, and is set HttpOnly,
Strict and only for the code routes."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi import Response

from src.api.google_cookies import PendingSignup, issue_signup_cookie
from src.api.tokens import issue_token
from src.api.verify_cookie import (
    VERIFY_COOKIE_NAME,
    InvalidVerifyCookieError,
    VerifyClaims,
    delete_verify_cookie,
    issue_verify_cookie,
    read_verify_cookie,
    set_verify_cookie,
)
from tests.api.conftest import API_SECRET, api_settings

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _issue(user_id: int = 7, account_id: int = 3, now: datetime = NOW) -> str:
    return issue_verify_cookie(user_id=user_id, account_id=account_id, secret=API_SECRET, now=now)


def _signed(payload: dict[str, Any]) -> str:
    key = hmac.new(API_SECRET.encode(), b"email-verify", hashlib.sha256).digest()
    return jwt.encode(payload, key, algorithm="HS256")


def _payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sub": "7",
        "acct": 3,
        "typ": "emailverify",
        "iat": NOW,
        "exp": NOW + timedelta(hours=24),
    }
    payload.update(changes)
    return {key: value for key, value in payload.items() if value is not None}


def test_a_cookie_reads_back_its_user_and_account() -> None:
    assert read_verify_cookie(_issue(), secret=API_SECRET, now=NOW) == VerifyClaims(
        user_id=7, account_id=3
    )


def test_a_cookie_works_for_24_hours() -> None:
    value = _issue()
    assert read_verify_cookie(value, secret=API_SECRET, now=NOW + timedelta(hours=23, minutes=59))
    with pytest.raises(InvalidVerifyCookieError):
        read_verify_cookie(value, secret=API_SECRET, now=NOW + timedelta(hours=24))


def test_a_naive_time_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _issue(now=datetime(2026, 10, 27, 12, 0))  # noqa: DTZ001  (naive on purpose)


@pytest.mark.parametrize(
    "value",
    [
        "not-a-cookie",
        _signed(_payload(typ="pwreset")),
        _signed(_payload(sub="seven")),
        _signed(_payload(acct=True)),
        _signed(_payload(acct="3")),
        _signed(_payload(sub=None)),
    ],
    ids=["damaged", "other-kind", "sub-not-a-number", "acct-a-bool", "acct-a-string", "no-sub"],
)
def test_a_damaged_or_odd_cookie_is_refused_without_repeating_it(value: str) -> None:
    with pytest.raises(InvalidVerifyCookieError) as caught:
        read_verify_cookie(value, secret=API_SECRET, now=NOW)
    assert value not in str(caught.value)


def test_another_secret_a_login_token_or_a_google_cookie_is_refused() -> None:
    login_token = issue_token(
        user_id=7, account_id=3, token_version=0, secret=API_SECRET, ttl_minutes=60, now=NOW
    )
    google = issue_signup_cookie(
        PendingSignup(subject="1", email="ann@gmail.com"), secret=API_SECRET, now=NOW
    )
    for value, secret in ((_issue(), API_SECRET + "x"), (login_token, API_SECRET)):
        with pytest.raises(InvalidVerifyCookieError):
            read_verify_cookie(value, secret=secret, now=NOW)
    with pytest.raises(InvalidVerifyCookieError):
        read_verify_cookie(google, secret=API_SECRET, now=NOW)


def test_the_cookie_is_httponly_strict_and_only_for_the_code_routes() -> None:
    response = Response()
    set_verify_cookie(response, api_settings(), user_id=7, account_id=3, now=NOW)
    header = response.headers["set-cookie"]
    assert header.startswith(f"{VERIFY_COOKIE_NAME}=")
    lowered = header.lower()
    for part in (
        "httponly",
        "samesite=strict",
        "path=/auth/verify-email",
        "max-age=86400",
        "secure",
    ):
        assert part in lowered


def test_deleting_the_cookie_expires_it_on_the_same_path() -> None:
    response = Response()
    delete_verify_cookie(response, api_settings())
    lowered = response.headers["set-cookie"].lower()
    assert lowered.startswith(f'{VERIFY_COOKIE_NAME}=""') and "max-age=0" in lowered
    assert "path=/auth/verify-email" in lowered
