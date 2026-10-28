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
    # The lowest win-model score the agent loop still drafts for, from 0 to 1. The score is
    # the model's estimate of a made-up label, so the value is a choice, not a measurement.
    agent_win_threshold: float = Field(default=0.30, ge=0, le=1, allow_inf_nan=False)

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


MIN_TOKEN_SECRET_CHARACTERS = 32
MAX_ACCOUNT_NAME_CHARACTERS = 200  # the length of `accounts.name`
# Allowing one of these would open sign-up to anyone with a free mail address.
PUBLIC_MAIL_DOMAINS = frozenset(
    {
        "aol.com",
        "gmail.com",
        "gmx.com",
        "googlemail.com",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mail.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "proton.me",
        "protonmail.com",
        "yahoo.com",
        "yandex.com",
        "zoho.com",
    }
)


def _split_list(value: str) -> list[str]:
    """The trimmed, lowercased, non-empty items of a comma-separated value."""
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _is_email_shaped(value: str) -> bool:
    """The shape `normalize_email` in `src/api/schemas.py` accepts: one `@`, text on both sides,
    no whitespace."""
    local, at, domain = value.partition("@")
    return (
        bool(at and local and domain) and "@" not in domain and not any(c.isspace() for c in value)
    )


def _is_domain_shaped(value: str) -> bool:
    """A bare domain with at least one dot, such as `clinic.example`; no `@`, no scheme."""
    labels = value.split(".")
    return len(labels) >= 2 and all(labels) and not any(c in value for c in "@/:")


class ApiSettings(BaseSettings):
    """Configuration of the web API's login. Loaded only when the API app is built.

    It is separate from `Settings` so that the pipelines and their tests need no token secret.
    The secret signs the login tokens; the lifetime is how long one token stays valid; the
    cookie flag is set false only for local development over plain HTTP. The upload limit is
    the largest file, in bytes, one upload may send. The login limit refuses a login when the
    typed email or the client IP has this many failed logins inside the window (in minutes).
    The mail keys say how the code emails leave the app; one email address gets at most
    `password_reset_per_email_per_hour` reset codes an hour. An emailed code lives
    `email_code_ttl_minutes` and dies after `email_code_max_wrong_tries` wrong tries; a user gets
    a new code at most every `email_code_resend_seconds` and `email_code_per_user_per_hour` an
    hour; the app sends at most `mail_daily_limit` emails a UTC day.

    Every sign-up joins the one account named `shared_account_name`. The users whose email is in
    `admin_emails` are admins; nobody else is. Sign-up is open only to those emails and to the
    ones in `signup_allowed_emails` or under a domain in `signup_allowed_domains`. The three lists
    are comma-separated; their emails are personal data, so a rejected value is never shown.

    With `individual_signup_enabled`, any other email may sign up too and gets a personal account
    of its own. Each personal account may upload `individual_monthly_uploads` letters a UTC
    month, and all personal accounts together `individual_daily_uploads` a UTC day.
    """

    # `hide_input_in_errors` keeps a rejected secret out of the validation error's text.
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )

    auth_token_secret: SecretStr
    shared_account_name: str = Field(min_length=1, max_length=MAX_ACCOUNT_NAME_CHARACTERS)
    admin_emails: str
    signup_allowed_emails: str = ""
    signup_allowed_domains: str = ""
    individual_signup_enabled: bool = False
    individual_monthly_uploads: int = Field(default=5, ge=1)
    individual_daily_uploads: int = Field(default=10, ge=1)
    auth_token_ttl_minutes: int = Field(default=60, ge=1)
    auth_cookie_secure: bool = True
    upload_max_bytes: int = Field(default=1_000_000, ge=1)
    login_limit_window_minutes: int = Field(default=15, ge=1)
    login_limit_per_email: int = Field(default=5, ge=1)
    login_limit_per_ip: int = Field(default=20, ge=1)
    # The address the browser opens the app at; Google sign-in addresses are built from it, never
    # from the request's `Host` header. Optional here so that settings load without it; `create_app`
    # refuses to start without it.
    app_public_url: str | None = None
    # `outbox` writes each email as a file (local development only); `smtp` sends it.
    mail_backend: Literal["outbox", "smtp"] = "outbox"
    mail_outbox_dir: Path = Path("var/outbox")
    mail_smtp_host: str | None = None
    mail_smtp_port: int = Field(default=587, ge=1, le=65535)
    mail_smtp_username: str | None = None
    mail_smtp_password: SecretStr | None = None
    mail_from: str | None = None
    password_reset_per_email_per_hour: int = Field(default=3, ge=1)
    email_code_ttl_minutes: int = Field(default=10, ge=1)
    email_code_max_wrong_tries: int = Field(default=5, ge=1)
    email_code_resend_seconds: int = Field(default=60, ge=1)
    email_code_per_user_per_hour: int = Field(default=5, ge=1)
    # Below the mail provider's free daily allowance (Brevo's free plan: about 300 a day).
    mail_daily_limit: int = Field(default=250, ge=1)
    # "Continue with Google" is off unless both the client ID and the secret are set. The URLs
    # are Google's (from its discovery document); Google signs ID tokens with either issuer.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"  # noqa: S105  (a URL)
    google_jwks_url: str = "https://www.googleapis.com/oauth2/v3/certs"
    # Comma-separated, so the environment can hold it as one plain value.
    google_issuers: str = "https://accounts.google.com,accounts.google.com"

    @property
    def admin_email_set(self) -> frozenset[str]:
        """The admins' emails, trimmed and lowercased like a stored email."""
        return frozenset(_split_list(self.admin_emails))

    @property
    def signup_allowed_email_set(self) -> frozenset[str]:
        """The other emails that may sign up, trimmed and lowercased."""
        return frozenset(_split_list(self.signup_allowed_emails))

    @property
    def signup_allowed_domain_set(self) -> frozenset[str]:
        """The domains whose every email may sign up, lowercased."""
        return frozenset(_split_list(self.signup_allowed_domains))

    @field_validator("admin_emails")
    @classmethod
    def _admin_emails_must_be_emails(cls, value: str) -> str:
        emails = _split_list(value)
        if not emails:
            raise ValueError("ADMIN_EMAILS names no email")
        if not all(_is_email_shaped(email) for email in emails):
            raise ValueError("ADMIN_EMAILS holds an item that is not an email address")
        return value

    @field_validator("signup_allowed_emails")
    @classmethod
    def _allowed_emails_must_be_emails(cls, value: str) -> str:
        if not all(_is_email_shaped(email) for email in _split_list(value)):
            raise ValueError("SIGNUP_ALLOWED_EMAILS holds an item that is not an email address")
        return value

    @field_validator("signup_allowed_domains")
    @classmethod
    def _allowed_domains_must_be_private_domains(cls, value: str) -> str:
        domains = _split_list(value)
        if not all(_is_domain_shaped(domain) for domain in domains):
            raise ValueError("SIGNUP_ALLOWED_DOMAINS holds an item that is not a bare domain")
        public = sorted(PUBLIC_MAIL_DOMAINS.intersection(domains))
        if public:
            # A public domain is not personal data, so naming it helps the operator.
            raise ValueError(
                f"SIGNUP_ALLOWED_DOMAINS must not hold a public mail domain: {', '.join(public)}"
            )
        return value

    @property
    def google_enabled(self) -> bool:
        """True when "Continue with Google" is configured (the validator makes it both or none)."""
        return self.google_client_id is not None

    @property
    def google_issuer_list(self) -> tuple[str, ...]:
        """The accepted `iss` values of a Google ID token."""
        return tuple(issuer.strip() for issuer in self.google_issuers.split(",") if issuer.strip())

    @field_validator("google_auth_url", "google_token_url", "google_jwks_url")
    @classmethod
    def _google_url_must_be_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("a Google URL must start with https://")
        return value

    @model_validator(mode="after")
    def _google_needs_both_keys(self) -> Self:
        values = {
            "GOOGLE_CLIENT_ID": self.google_client_id,
            "GOOGLE_CLIENT_SECRET": self.google_client_secret,
        }
        missing = [key for key, value in values.items() if value is None]
        if len(missing) == 1:
            raise ValueError(f"Google sign-in needs both keys; {missing[0]} is not set")
        if not self.google_issuer_list:
            raise ValueError("GOOGLE_ISSUERS names no issuer")
        return self

    @field_validator("app_public_url")
    @classmethod
    def _public_url_must_be_http(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("the public URL must start with http:// or https://")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _smtp_needs_its_keys(self) -> Self:
        if self.mail_backend != "smtp":
            return self
        values = {
            "MAIL_SMTP_HOST": self.mail_smtp_host,
            "MAIL_SMTP_USERNAME": self.mail_smtp_username,
            "MAIL_SMTP_PASSWORD": self.mail_smtp_password,
            "MAIL_FROM": self.mail_from,
        }
        missing = [key for key, value in values.items() if value is None]
        if missing:
            raise ValueError(f"MAIL_BACKEND=smtp needs {', '.join(missing)}")
        return self

    @field_validator("auth_token_secret")
    @classmethod
    def _secret_must_be_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().strip()) < MIN_TOKEN_SECRET_CHARACTERS:
            raise ValueError(
                f"the token secret must have at least {MIN_TOKEN_SECRET_CHARACTERS} characters"
            )
        return value


def load_api_settings(env_file: str | None = ".env") -> ApiSettings:
    """Build the API settings from the environment, optionally reading an env file."""
    return ApiSettings(_env_file=env_file)  # type: ignore[call-arg]
