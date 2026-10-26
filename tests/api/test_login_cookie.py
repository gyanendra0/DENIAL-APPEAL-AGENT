"""The login cookie: its attributes and the token version inside it."""

from fastapi import Response

from src.api.dependencies import AUTH_COOKIE_NAME
from src.api.login_cookie import set_login_cookie
from src.api.tokens import TokenClaims, read_token
from tests.api.conftest import API_SECRET, API_TTL_MINUTES, api_settings


def test_cookie_carries_a_token_with_the_user_and_the_version() -> None:
    response = Response()
    set_login_cookie(response, api_settings(), user_id=7, account_id=3, token_version=4)
    header = response.headers["set-cookie"]
    token = header.split(";")[0].removeprefix(f"{AUTH_COOKIE_NAME}=")
    assert read_token(token, secret=API_SECRET) == TokenClaims(
        user_id=7, account_id=3, token_version=4
    )


def test_cookie_is_httponly_strict_secure_and_lives_as_long_as_the_token() -> None:
    response = Response()
    set_login_cookie(response, api_settings(), user_id=7, account_id=3, token_version=0)
    header = response.headers["set-cookie"].lower()
    for part in ("httponly", "samesite=strict", "secure", "path=/"):
        assert part in header
    assert f"max-age={API_TTL_MINUTES * 60}" in header
