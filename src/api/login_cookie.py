"""The login cookie: a fresh token in an HttpOnly, SameSite=Strict cookie.

Set at sign-up, at login and after a password change (with the raised token version).
"""

from datetime import UTC, datetime

from fastapi import Response

from src.api.dependencies import AUTH_COOKIE_NAME
from src.api.tokens import issue_token
from src.config.settings import ApiSettings


def set_login_cookie(
    response: Response,
    settings: ApiSettings,
    *,
    user_id: int,
    account_id: int,
    token_version: int,
) -> None:
    """Put a new login token for the user on `response`, valid for the configured lifetime."""
    token = issue_token(
        user_id=user_id,
        account_id=account_id,
        token_version=token_version,
        secret=settings.auth_token_secret.get_secret_value(),
        ttl_minutes=settings.auth_token_ttl_minutes,
        now=datetime.now(UTC),
    )
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=settings.auth_token_ttl_minutes * 60,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )
