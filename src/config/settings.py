"""Settings read from environment variables.

A missing or invalid required key fails at load time with an error naming the key.
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Values come from the environment or `.env`."""

    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: str

    @field_validator("database_url")
    @classmethod
    def _must_be_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("database_url must be a postgresql URL")
        return value


def load_settings(env_file: str | None = ".env") -> Settings:
    """Build settings from the environment, optionally reading an env file."""
    return Settings(_env_file=env_file)  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once."""
    return load_settings()
