import pytest
from pydantic import ValidationError

from src.config.settings import get_settings, load_settings

URL = "postgresql+psycopg://user:pw@localhost:5432/app"


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("APP_ENV", "test")

    settings = load_settings(env_file=None)

    assert settings.database_url == URL
    assert settings.app_env == "test"
    assert settings.log_level == "INFO"


def test_missing_database_url_fails_naming_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        load_settings(env_file=None)


def test_rejects_non_postgres_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///local.db")

    with pytest.raises(ValidationError, match="postgresql"):
        load_settings(env_file=None)


def test_rejects_unknown_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError, match="app_env"):
        load_settings(env_file=None)


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()
