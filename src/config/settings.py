"""Settings read from environment variables.

A missing or invalid required key fails at load time with an error naming the key.
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

UsdAmount = Annotated[Decimal, Field(ge=0)]
NonEmptyText = Annotated[str, Field(min_length=1)]


class Settings(BaseSettings):
    """All runtime configuration. Values come from the environment or `.env`."""

    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: str
    # The folder that holds the versioned prompt files, relative to where a command is run.
    prompt_dir: Path = Path("config/prompts")
    # The folder a trained model and its facts file are written to and read from.
    model_dir: Path = Path("models")
    # The smallest total allowed charge the rules let through, in US dollars. Assumed: no
    # source gives this value.
    rules_amount_floor_usd: UsdAmount = Decimal("25")

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


class LlmGatewaySettings(BaseSettings):
    """Configuration of the LLM gateway. Loaded only when a gateway is built.

    It is separate from `Settings` so that the pipelines and the tests, which call no model,
    need no API key. The primary and the fallback provider are each a base URL of a service
    that speaks the OpenAI chat format, a model name, a key and two prices. Prices are in US
    dollars per million tokens. The timeout is the longest one call may take, for both
    providers. Embeddings use the primary provider's base URL and key, with their own model
    and price; they have no fallback.
    """

    # `hide_input_in_errors` keeps a rejected value (a key, a URL holding a password) out of
    # the validation error's text.
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )

    llm_monthly_budget_usd: UsdAmount
    llm_timeout_seconds: float = Field(gt=0)

    llm_primary_base_url: str
    llm_primary_model: NonEmptyText
    llm_primary_api_key: SecretStr
    llm_primary_input_usd_per_mtok: UsdAmount
    llm_primary_output_usd_per_mtok: UsdAmount

    llm_embedding_model: NonEmptyText
    llm_embedding_usd_per_mtok: UsdAmount

    llm_fallback_base_url: str | None = None
    llm_fallback_model: NonEmptyText | None = None
    llm_fallback_api_key: SecretStr | None = None
    llm_fallback_input_usd_per_mtok: UsdAmount = Decimal("0")
    llm_fallback_output_usd_per_mtok: UsdAmount = Decimal("0")

    @field_validator("llm_primary_api_key", "llm_fallback_api_key")
    @classmethod
    def _key_must_not_be_blank(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("an API key must not be blank")
        return value

    @field_validator("llm_primary_base_url", "llm_fallback_base_url")
    @classmethod
    def _must_be_http(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("a base URL must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _fallback_is_whole_or_absent(self) -> Self:
        if (self.llm_fallback_base_url is None) != (self.llm_fallback_model is None):
            raise ValueError(
                "LLM_FALLBACK_BASE_URL and LLM_FALLBACK_MODEL must be set together or not at all"
            )
        if self.llm_fallback_api_key is not None and self.llm_fallback_base_url is None:
            raise ValueError("LLM_FALLBACK_API_KEY is set but LLM_FALLBACK_BASE_URL is not")
        return self

    @property
    def has_fallback(self) -> bool:
        """True when a fallback provider is configured."""
        return self.llm_fallback_base_url is not None


def load_llm_gateway_settings(env_file: str | None = ".env") -> LlmGatewaySettings:
    """Build the gateway settings from the environment, optionally reading an env file."""
    return LlmGatewaySettings(_env_file=env_file)  # type: ignore[call-arg]
