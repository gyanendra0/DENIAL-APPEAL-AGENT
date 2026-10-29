"""The login cookie: its attributes and the token version inside it, and clearing it."""

from datetime import UTC, datetime, timedelta

from fastapi import Response

from src.api.dependencies import AUTH_COOKIE_NAME
from src.api.login_cookie import clear_login_cookie, set_login_cookie
from src.api.tokens import TokenClaims, read_token
from tests.api.conftest import API_SECRET, API_TTL_MINUTES, api_settings


def test_cookie_carries_a_token_with_the_user_and_the_version() -> None:
    response = Response()
    before = datetime.now(UTC).replace(microsecond=0)
    set_login_cookie(response, api_settings(), user_id=7, account_id=3, token_version=4)
    header = response.headers["set-cookie"]
    token = header.split(";")[0].removeprefix(f"{AUTH_COOKIE_NAME}=")
    claims = read_token(token, secret=API_SECRET)
    assert claims == TokenClaims(
        user_id=7, account_id=3, token_version=4, issued_at=claims.issued_at
    )
    assert before <= claims.issued_at <= before + timedelta(seconds=5)


def test_cookie_is_httponly_strict_secure_and_lives_as_long_as_the_token() -> None:
    response = Response()
    set_login_cookie(response, api_settings(), user_id=7, account_id=3, token_version=0)
    header = response.headers["set-cookie"].lower()
    for part in ("httponly", "samesite=strict", "secure", "path=/"):
        assert part in header
    assert f"max-age={API_TTL_MINUTES * 60}" in header


def test_clearing_expires_the_cookie_with_the_same_attributes() -> None:
    response = Response()
    clear_login_cookie(response, api_settings())
    header = response.headers["set-cookie"].lower()
    assert header.startswith(f"{AUTH_COOKIE_NAME}=".lower())
    for part in ("max-age=0", "httponly", "samesite=strict", "secure", "path=/"):
        assert part in header
