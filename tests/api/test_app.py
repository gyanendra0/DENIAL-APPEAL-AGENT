"""The app factory: settings and database from the environment, a bad secret fails at startup."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.orm import sessionmaker

from src.api.app import create_app
from src.config.settings import ApiSettings, get_settings

URL = "postgresql+psycopg://made:up@localhost:5433/none"
SECRET = "a-made-up-factory-secret-of-forty-chars!"  # noqa: S105  (made up)


@pytest.fixture
def no_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run where no `.env` exists, so only the variables set by the test count."""
    monkeypatch.chdir(tmp_path)
    for key in ("AUTH_TOKEN_SECRET", "AUTH_TOKEN_TTL_MINUTES", "AUTH_COOKIE_SECURE"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()


@pytest.mark.usefixtures("no_env_file")
def test_builds_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("AUTH_TOKEN_SECRET", SECRET)
    app = create_app()
    settings: ApiSettings = app.state.api_settings
    assert settings.auth_token_secret.get_secret_value() == SECRET
    assert app.state.session_factory.kw["bind"].url.database == "none"
    get_settings.cache_clear()


@pytest.mark.usefixtures("no_env_file")
def test_missing_secret_fails_at_startup_naming_the_key() -> None:
    with pytest.raises(ValidationError, match="auth_token_secret"):
        create_app()


@pytest.mark.usefixtures("no_env_file")
def test_short_secret_fails_at_startup_without_showing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "short-made-up-value")
    with pytest.raises(ValidationError, match="auth_token_secret") as caught:
        create_app()
    assert "short-made-up-value" not in str(caught.value)


def test_routes_are_registered() -> None:
    settings = ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(SECRET), _env_file=None
    )
    app = create_app(settings, sessionmaker())
    # Included routers are nested in `app.routes`; the published schema lists every path.
    assert set(app.openapi()["paths"]) == {
        "/health",
        "/auth/signup",
        "/auth/login",
        "/auth/logout",
        "/me",
        "/users/{user_id}",
    }
