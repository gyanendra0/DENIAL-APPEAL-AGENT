from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from src.config.settings import (
    get_settings,
    load_api_settings,
    load_llm_gateway_settings,
    load_settings,
)

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


def test_prompt_folder_defaults_to_the_one_in_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("PROMPT_DIR", raising=False)

    assert load_settings(env_file=None).prompt_dir == Path("config/prompts")


def test_prompt_folder_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("PROMPT_DIR", "/made/up/prompts")

    assert load_settings(env_file=None).prompt_dir == Path("/made/up/prompts")


def test_model_folder_defaults_to_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("MODEL_DIR", raising=False)

    assert load_settings(env_file=None).model_dir == Path("models")


def test_model_folder_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("MODEL_DIR", "/made/up/models")

    assert load_settings(env_file=None).model_dir == Path("/made/up/models")


def test_amount_floor_defaults_to_an_exact_25(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("RULES_AMOUNT_FLOOR_USD", raising=False)

    floor = load_settings(env_file=None).rules_amount_floor_usd

    assert floor == Decimal("25")
    assert isinstance(floor, Decimal)


def test_amount_floor_is_read_from_the_environment_as_an_exact_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("RULES_AMOUNT_FLOOR_USD", "50.10")

    floor = load_settings(env_file=None).rules_amount_floor_usd

    assert isinstance(floor, Decimal)
    assert str(floor) == "50.10"


def test_an_amount_floor_of_zero_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("RULES_AMOUNT_FLOOR_USD", "0")

    assert load_settings(env_file=None).rules_amount_floor_usd == Decimal("0")


@pytest.mark.parametrize("value", ["-1", "-0.01", "abc", ""], ids=["-1", "-0.01", "text", "empty"])
def test_rejects_an_amount_floor_that_is_negative_or_not_a_number(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("RULES_AMOUNT_FLOOR_USD", value)

    with pytest.raises(ValidationError, match="rules_amount_floor_usd"):
        load_settings(env_file=None)


def test_win_threshold_defaults_to_0_30(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("AGENT_WIN_THRESHOLD", raising=False)

    assert load_settings(env_file=None).agent_win_threshold == 0.30


@pytest.mark.parametrize(("value", "threshold"), [("0", 0.0), ("0.45", 0.45), ("1", 1.0)])
def test_win_threshold_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, value: str, threshold: float
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("AGENT_WIN_THRESHOLD", value)

    assert load_settings(env_file=None).agent_win_threshold == threshold


@pytest.mark.parametrize("value", ["-0.01", "1.01", "nan", "inf", "abc", ""])
def test_rejects_a_win_threshold_outside_0_to_1_and_names_the_key(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("AGENT_WIN_THRESHOLD", value)

    with pytest.raises(ValidationError, match="agent_win_threshold"):
        load_settings(env_file=None)


# A made-up value. No real key ever appears in a test.
API_KEY = "test-key-not-real"
FALLBACK_KEY = "test-fallback-key-not-real"

REQUIRED_GATEWAY_ENV = {
    "LLM_MONTHLY_BUDGET_USD": "5.00",
    "LLM_TIMEOUT_SECONDS": "30",
    "LLM_PRIMARY_BASE_URL": "https://llm.example.com/v1",
    "LLM_PRIMARY_MODEL": "made-up-small-model",
    "LLM_PRIMARY_API_KEY": API_KEY,
    "LLM_PRIMARY_INPUT_USD_PER_MTOK": "1.00",
    "LLM_PRIMARY_OUTPUT_USD_PER_MTOK": "5.00",
    "LLM_EMBEDDING_MODEL": "made-up-embedding-model",
    "LLM_EMBEDDING_USD_PER_MTOK": "0.02",
}
OPTIONAL_GATEWAY_KEYS = (
    "LLM_FALLBACK_BASE_URL",
    "LLM_FALLBACK_MODEL",
    "LLM_FALLBACK_API_KEY",
    "LLM_FALLBACK_INPUT_USD_PER_MTOK",
    "LLM_FALLBACK_OUTPUT_USD_PER_MTOK",
)


@pytest.fixture
def gateway_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Set the required gateway keys and clear the optional ones."""
    for key, value in REQUIRED_GATEWAY_ENV.items():
        monkeypatch.setenv(key, value)
    for key in OPTIONAL_GATEWAY_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_gateway_settings_read_the_required_values(gateway_env: pytest.MonkeyPatch) -> None:
    settings = load_llm_gateway_settings(env_file=None)

    assert settings.llm_monthly_budget_usd == Decimal("5.00")
    assert settings.llm_timeout_seconds == 30.0
    assert settings.llm_primary_base_url == "https://llm.example.com/v1"
    assert settings.llm_primary_model == "made-up-small-model"
    assert settings.llm_primary_api_key.get_secret_value() == API_KEY
    assert settings.llm_primary_input_usd_per_mtok == Decimal("1.00")
    assert settings.llm_primary_output_usd_per_mtok == Decimal("5.00")
    assert settings.llm_embedding_model == "made-up-embedding-model"
    assert settings.llm_embedding_usd_per_mtok == Decimal("0.02")


def test_gateway_money_values_are_exact_decimals(gateway_env: pytest.MonkeyPatch) -> None:
    gateway_env.setenv("LLM_PRIMARY_INPUT_USD_PER_MTOK", "0.1")

    settings = load_llm_gateway_settings(env_file=None)

    assert isinstance(settings.llm_monthly_budget_usd, Decimal)
    # A float would give 0.30000000000000004 here.
    assert settings.llm_primary_input_usd_per_mtok * 3 == Decimal("0.3")


@pytest.mark.parametrize("key", sorted(REQUIRED_GATEWAY_ENV))
def test_missing_required_gateway_key_fails_naming_the_key(
    gateway_env: pytest.MonkeyPatch, key: str
) -> None:
    gateway_env.delenv(key)

    with pytest.raises(ValidationError, match=key.lower()):
        load_llm_gateway_settings(env_file=None)


@pytest.mark.parametrize("key", sorted(REQUIRED_GATEWAY_ENV))
def test_empty_required_gateway_key_counts_as_missing(
    gateway_env: pytest.MonkeyPatch, key: str
) -> None:
    gateway_env.setenv(key, "")

    with pytest.raises(ValidationError, match=key.lower()):
        load_llm_gateway_settings(env_file=None)


@pytest.mark.parametrize("key", ["LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL", "LLM_EMBEDDING_MODEL"])
def test_rejects_a_blank_key_or_model_name(gateway_env: pytest.MonkeyPatch, key: str) -> None:
    gateway_env.setenv(key, "   ")

    with pytest.raises(ValidationError, match=key.lower()):
        load_llm_gateway_settings(env_file=None)


@pytest.mark.parametrize(
    "key",
    [
        "LLM_MONTHLY_BUDGET_USD",
        "LLM_PRIMARY_INPUT_USD_PER_MTOK",
        "LLM_PRIMARY_OUTPUT_USD_PER_MTOK",
        "LLM_EMBEDDING_USD_PER_MTOK",
        "LLM_FALLBACK_INPUT_USD_PER_MTOK",
        "LLM_FALLBACK_OUTPUT_USD_PER_MTOK",
    ],
)
def test_rejects_a_negative_budget_or_price(gateway_env: pytest.MonkeyPatch, key: str) -> None:
    gateway_env.setenv(key, "-0.01")

    with pytest.raises(ValidationError, match=key.lower()):
        load_llm_gateway_settings(env_file=None)


def test_rejects_a_budget_that_is_not_a_number(gateway_env: pytest.MonkeyPatch) -> None:
    gateway_env.setenv("LLM_MONTHLY_BUDGET_USD", "five")

    with pytest.raises(ValidationError, match="llm_monthly_budget_usd"):
        load_llm_gateway_settings(env_file=None)


@pytest.mark.parametrize("value", ["0", "-1", "soon"])
def test_rejects_a_timeout_that_is_not_a_positive_number(
    gateway_env: pytest.MonkeyPatch, value: str
) -> None:
    gateway_env.setenv("LLM_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError, match="llm_timeout_seconds"):
        load_llm_gateway_settings(env_file=None)


def test_reads_a_timeout_with_a_fraction_of_a_second(gateway_env: pytest.MonkeyPatch) -> None:
    gateway_env.setenv("LLM_TIMEOUT_SECONDS", "7.5")

    assert load_llm_gateway_settings(env_file=None).llm_timeout_seconds == 7.5


def test_api_key_is_not_shown_when_settings_are_printed(gateway_env: pytest.MonkeyPatch) -> None:
    gateway_env.setenv("LLM_FALLBACK_BASE_URL", "http://localhost:11434")
    gateway_env.setenv("LLM_FALLBACK_MODEL", "made-up-local-model")
    gateway_env.setenv("LLM_FALLBACK_API_KEY", FALLBACK_KEY)

    settings = load_llm_gateway_settings(env_file=None)

    for shown in (repr(settings), str(settings), settings.model_dump_json()):
        assert API_KEY not in shown
        assert FALLBACK_KEY not in shown


def test_api_key_is_not_shown_in_a_validation_error(gateway_env: pytest.MonkeyPatch) -> None:
    gateway_env.setenv("LLM_FALLBACK_MODEL", "made-up-local-model")

    with pytest.raises(ValidationError) as caught:
        load_llm_gateway_settings(env_file=None)

    assert API_KEY not in str(caught.value)
    assert API_KEY not in repr(caught.value)


def test_a_rejected_value_is_not_shown_in_the_error(gateway_env: pytest.MonkeyPatch) -> None:
    # A field error prints the rejected value unless inputs are hidden, and a URL can hold a
    # password.
    gateway_env.setenv("LLM_FALLBACK_BASE_URL", "ftp://user:made-up-password@llm.example.com")
    gateway_env.setenv("LLM_FALLBACK_MODEL", "made-up-local-model")

    with pytest.raises(ValidationError, match="llm_fallback_base_url") as caught:
        load_llm_gateway_settings(env_file=None)

    assert "made-up-password" not in str(caught.value)


def test_no_fallback_keys_means_no_fallback(gateway_env: pytest.MonkeyPatch) -> None:
    settings = load_llm_gateway_settings(env_file=None)

    assert settings.has_fallback is False
    assert settings.llm_fallback_base_url is None
    assert settings.llm_fallback_model is None
    assert settings.llm_fallback_api_key is None


def test_fallback_is_free_and_needs_no_key_by_default(gateway_env: pytest.MonkeyPatch) -> None:
    gateway_env.setenv("LLM_FALLBACK_BASE_URL", "http://localhost:11434")
    gateway_env.setenv("LLM_FALLBACK_MODEL", "made-up-local-model")

    settings = load_llm_gateway_settings(env_file=None)

    assert settings.has_fallback is True
    assert settings.llm_fallback_base_url == "http://localhost:11434"
    assert settings.llm_fallback_model == "made-up-local-model"
    assert settings.llm_fallback_api_key is None
    assert settings.llm_fallback_input_usd_per_mtok == Decimal("0")
    assert settings.llm_fallback_output_usd_per_mtok == Decimal("0")


def test_reads_a_paid_fallback_with_its_key_and_prices(gateway_env: pytest.MonkeyPatch) -> None:
    gateway_env.setenv("LLM_FALLBACK_BASE_URL", "https://llm.example.com/v1")
    gateway_env.setenv("LLM_FALLBACK_MODEL", "made-up-hosted-model")
    gateway_env.setenv("LLM_FALLBACK_API_KEY", FALLBACK_KEY)
    gateway_env.setenv("LLM_FALLBACK_INPUT_USD_PER_MTOK", "0.15")
    gateway_env.setenv("LLM_FALLBACK_OUTPUT_USD_PER_MTOK", "0.60")

    settings = load_llm_gateway_settings(env_file=None)

    assert settings.llm_fallback_api_key is not None
    assert settings.llm_fallback_api_key.get_secret_value() == FALLBACK_KEY
    assert settings.llm_fallback_input_usd_per_mtok == Decimal("0.15")
    assert settings.llm_fallback_output_usd_per_mtok == Decimal("0.60")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("LLM_FALLBACK_BASE_URL", "http://localhost:11434"),
        ("LLM_FALLBACK_MODEL", "made-up-local-model"),
    ],
)
def test_rejects_half_a_fallback(gateway_env: pytest.MonkeyPatch, key: str, value: str) -> None:
    gateway_env.setenv(key, value)

    with pytest.raises(ValidationError, match="must be set together"):
        load_llm_gateway_settings(env_file=None)


def test_rejects_a_fallback_key_without_a_fallback(gateway_env: pytest.MonkeyPatch) -> None:
    gateway_env.setenv("LLM_FALLBACK_API_KEY", FALLBACK_KEY)

    with pytest.raises(ValidationError, match="LLM_FALLBACK_BASE_URL is not") as caught:
        load_llm_gateway_settings(env_file=None)

    assert FALLBACK_KEY not in str(caught.value)


@pytest.mark.parametrize("key", ["LLM_PRIMARY_BASE_URL", "LLM_FALLBACK_BASE_URL"])
def test_rejects_a_base_url_that_is_not_http(gateway_env: pytest.MonkeyPatch, key: str) -> None:
    gateway_env.setenv("LLM_FALLBACK_MODEL", "made-up-local-model")
    gateway_env.setenv(key, "localhost:11434")

    with pytest.raises(ValidationError, match=f"(?s){key.lower()}.*http://"):
        load_llm_gateway_settings(env_file=None)


def test_empty_optional_fallback_keys_count_as_not_set(gateway_env: pytest.MonkeyPatch) -> None:
    for key in OPTIONAL_GATEWAY_KEYS:
        gateway_env.setenv(key, "")

    settings = load_llm_gateway_settings(env_file=None)

    assert settings.has_fallback is False
    assert settings.llm_fallback_input_usd_per_mtok == Decimal("0")


def test_app_settings_need_no_gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", URL)
    for key in (*REQUIRED_GATEWAY_ENV, *OPTIONAL_GATEWAY_KEYS):
        monkeypatch.delenv(key, raising=False)

    assert load_settings(env_file=None).database_url == URL


def test_env_example_lists_every_gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (*REQUIRED_GATEWAY_ENV, *OPTIONAL_GATEWAY_KEYS):
        monkeypatch.delenv(key, raising=False)
    example = Path(__file__).parents[2] / ".env.example"
    listed = {line.split("=", 1)[0] for line in example.read_text().splitlines() if "=" in line}

    settings = load_llm_gateway_settings(env_file=str(example))

    assert {
        *REQUIRED_GATEWAY_ENV,
        *OPTIONAL_GATEWAY_KEYS,
        "PROMPT_DIR",
        "MODEL_DIR",
        "RULES_AMOUNT_FLOOR_USD",
    } <= listed
    assert settings.has_fallback is True
    assert settings.llm_fallback_api_key is not None
    # Worked out before the asserts, so a failure never prints the value of a real key.
    primary_is_placeholder = _is_placeholder(settings.llm_primary_api_key)
    fallback_is_placeholder = _is_placeholder(settings.llm_fallback_api_key)
    assert primary_is_placeholder, "LLM_PRIMARY_API_KEY in .env.example is not a placeholder"
    assert fallback_is_placeholder, "LLM_FALLBACK_API_KEY in .env.example is not a placeholder"


def _is_placeholder(key: SecretStr) -> bool:
    return key.get_secret_value().startswith("replace-with-")


# A made-up value of exactly 32 characters. No real secret ever appears in a test.
TOKEN_SECRET = "test-token-secret-not-real-32chr"  # noqa: S105  (made up, not a real secret)
LOGIN_LIMIT_KEYS = ("LOGIN_LIMIT_WINDOW_MINUTES", "LOGIN_LIMIT_PER_EMAIL", "LOGIN_LIMIT_PER_IP")
API_KEYS = ("AUTH_TOKEN_SECRET", "AUTH_TOKEN_TTL_MINUTES", "AUTH_COOKIE_SECURE", *LOGIN_LIMIT_KEYS)
MAIL_KEYS = (
    "APP_PUBLIC_URL",
    "MAIL_BACKEND",
    "MAIL_OUTBOX_DIR",
    "MAIL_SMTP_HOST",
    "MAIL_SMTP_PORT",
    "MAIL_SMTP_USERNAME",
    "MAIL_SMTP_PASSWORD",
    "MAIL_FROM",
    "PASSWORD_RESET_PER_EMAIL_PER_HOUR",
)
EMAIL_CODE_KEYS = (
    "EMAIL_CODE_TTL_MINUTES",
    "EMAIL_CODE_MAX_WRONG_TRIES",
    "EMAIL_CODE_RESEND_SECONDS",
    "EMAIL_CODE_PER_USER_PER_HOUR",
    "MAIL_DAILY_LIMIT",
)
GOOGLE_KEYS = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_AUTH_URL",
    "GOOGLE_TOKEN_URL",
    "GOOGLE_JWKS_URL",
    "GOOGLE_ISSUERS",
)
GOOGLE_ENV = {
    "GOOGLE_CLIENT_ID": "made-up-client-id.apps.example.test",
    "GOOGLE_CLIENT_SECRET": "made-up-google-client-secret",
}
SMTP_ENV = {
    "MAIL_BACKEND": "smtp",
    "MAIL_SMTP_HOST": "smtp.example.test",
    "MAIL_SMTP_USERNAME": "made-up-user",
    "MAIL_SMTP_PASSWORD": "made-up-mail-password",
    "MAIL_FROM": "sender@example.test",
}


@pytest.fixture
def api_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Set the token secret and clear the optional API keys."""
    for key in (*API_KEYS, *MAIL_KEYS, *GOOGLE_KEYS):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTH_TOKEN_SECRET", TOKEN_SECRET)
    return monkeypatch


def test_api_settings_read_the_secret_and_use_the_defaults(api_env: pytest.MonkeyPatch) -> None:
    settings = load_api_settings(env_file=None)

    assert len(TOKEN_SECRET) == 32
    assert settings.auth_token_secret.get_secret_value() == TOKEN_SECRET
    assert settings.auth_token_ttl_minutes == 60
    assert settings.auth_cookie_secure is True


def test_api_settings_read_the_lifetime_and_cookie_flag(api_env: pytest.MonkeyPatch) -> None:
    api_env.setenv("AUTH_TOKEN_TTL_MINUTES", "15")
    api_env.setenv("AUTH_COOKIE_SECURE", "false")

    settings = load_api_settings(env_file=None)

    assert settings.auth_token_ttl_minutes == 15
    assert settings.auth_cookie_secure is False


@pytest.mark.parametrize("value", [None, "", "   "], ids=["missing", "empty", "blank"])
def test_missing_token_secret_fails_naming_the_key(
    api_env: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        api_env.delenv("AUTH_TOKEN_SECRET")
    else:
        api_env.setenv("AUTH_TOKEN_SECRET", value)

    with pytest.raises(ValidationError, match="auth_token_secret"):
        load_api_settings(env_file=None)


def test_rejects_a_token_secret_under_32_characters_without_showing_it(
    api_env: pytest.MonkeyPatch,
) -> None:
    short = TOKEN_SECRET[:31]
    api_env.setenv("AUTH_TOKEN_SECRET", short)

    with pytest.raises(ValidationError, match="(?s)auth_token_secret.*32 characters") as caught:
        load_api_settings(env_file=None)

    assert short not in str(caught.value)
    assert short not in repr(caught.value)


def test_spaces_around_a_short_token_secret_do_not_count(api_env: pytest.MonkeyPatch) -> None:
    api_env.setenv("AUTH_TOKEN_SECRET", f"  {TOKEN_SECRET[:30]}  ")

    with pytest.raises(ValidationError, match="auth_token_secret"):
        load_api_settings(env_file=None)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "soon"])
def test_rejects_a_token_lifetime_that_is_not_a_whole_number_of_at_least_1(
    api_env: pytest.MonkeyPatch, value: str
) -> None:
    api_env.setenv("AUTH_TOKEN_TTL_MINUTES", value)

    with pytest.raises(ValidationError, match="auth_token_ttl_minutes"):
        load_api_settings(env_file=None)


def test_login_limit_defaults_to_5_per_email_and_20_per_ip_in_15_minutes(
    api_env: pytest.MonkeyPatch,
) -> None:
    settings = load_api_settings(env_file=None)

    assert settings.login_limit_window_minutes == 15
    assert settings.login_limit_per_email == 5
    assert settings.login_limit_per_ip == 20


def test_login_limit_is_read_from_the_environment(api_env: pytest.MonkeyPatch) -> None:
    api_env.setenv("LOGIN_LIMIT_WINDOW_MINUTES", "30")
    api_env.setenv("LOGIN_LIMIT_PER_EMAIL", "3")
    api_env.setenv("LOGIN_LIMIT_PER_IP", "1")

    settings = load_api_settings(env_file=None)

    assert settings.login_limit_window_minutes == 30
    assert settings.login_limit_per_email == 3
    assert settings.login_limit_per_ip == 1


@pytest.mark.parametrize("key", LOGIN_LIMIT_KEYS)
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "many"])
def test_rejects_a_login_limit_that_is_not_a_whole_number_of_at_least_1_and_names_the_key(
    api_env: pytest.MonkeyPatch, key: str, value: str
) -> None:
    api_env.setenv(key, value)

    with pytest.raises(ValidationError, match=key.lower()):
        load_api_settings(env_file=None)


def test_rejects_a_cookie_flag_that_is_not_true_or_false(api_env: pytest.MonkeyPatch) -> None:
    api_env.setenv("AUTH_COOKIE_SECURE", "sometimes")

    with pytest.raises(ValidationError, match="auth_cookie_secure"):
        load_api_settings(env_file=None)


def test_token_secret_is_not_shown_when_settings_are_printed(api_env: pytest.MonkeyPatch) -> None:
    settings = load_api_settings(env_file=None)

    for shown in (repr(settings), str(settings), settings.model_dump_json()):
        assert TOKEN_SECRET not in shown


def test_app_and_gateway_settings_need_no_api_key(gateway_env: pytest.MonkeyPatch) -> None:
    gateway_env.setenv("DATABASE_URL", URL)
    for key in API_KEYS:
        gateway_env.delenv(key, raising=False)

    assert load_settings(env_file=None).database_url == URL
    assert load_llm_gateway_settings(env_file=None).llm_primary_model == "made-up-small-model"


def test_env_example_lists_every_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = (*API_KEYS, *MAIL_KEYS, *EMAIL_CODE_KEYS, *GOOGLE_KEYS)
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    example = Path(__file__).parents[2] / ".env.example"
    listed = {line.split("=", 1)[0] for line in example.read_text().splitlines() if "=" in line}

    settings = load_api_settings(env_file=str(example))

    assert set(keys) <= listed
    # Removed with the reset link (migration 0022); a stale line would be ignored silently.
    assert "PASSWORD_RESET_TTL_MINUTES" not in listed
    assert settings.mail_backend == "outbox"
    # The Google keys are listed empty: no client secret, real or made up, and the button is off.
    assert not settings.google_enabled
    assert settings.google_client_secret is None
    # Worked out before the asserts, so a failure never prints the value of a real secret.
    secret_is_placeholder = _is_placeholder(settings.auth_token_secret)
    mail_password = settings.mail_smtp_password
    mail_password_is_placeholder = mail_password is not None and _is_placeholder(mail_password)
    assert secret_is_placeholder, "AUTH_TOKEN_SECRET in .env.example is not a placeholder"
    assert mail_password_is_placeholder, "MAIL_SMTP_PASSWORD in .env.example is not a placeholder"


def test_mail_and_reset_settings_default_to_the_outbox_and_3_an_hour(
    api_env: pytest.MonkeyPatch,
) -> None:
    settings = load_api_settings(env_file=None)

    assert settings.app_public_url is None
    assert (settings.mail_backend, settings.mail_outbox_dir) == ("outbox", Path("var/outbox"))
    assert settings.mail_smtp_port == 587
    assert settings.password_reset_per_email_per_hour == 3


def test_smtp_settings_are_read_from_the_environment(api_env: pytest.MonkeyPatch) -> None:
    for key, value in SMTP_ENV.items():
        api_env.setenv(key, value)
    api_env.setenv("MAIL_SMTP_PORT", "2525")

    settings = load_api_settings(env_file=None)

    assert settings.mail_backend == "smtp"
    assert (settings.mail_smtp_host, settings.mail_smtp_port) == ("smtp.example.test", 2525)
    assert settings.mail_smtp_password is not None
    assert settings.mail_smtp_password.get_secret_value() == "made-up-mail-password"
    assert "made-up-mail-password" not in repr(settings)


@pytest.mark.parametrize(
    "key", ["MAIL_SMTP_HOST", "MAIL_SMTP_USERNAME", "MAIL_SMTP_PASSWORD", "MAIL_FROM"]
)
def test_smtp_without_one_of_its_keys_fails_naming_it(
    api_env: pytest.MonkeyPatch, key: str
) -> None:
    for name, value in SMTP_ENV.items():
        api_env.setenv(name, value)
    api_env.setenv(key, "")

    with pytest.raises(ValidationError, match=key) as caught:
        load_api_settings(env_file=None)
    assert "made-up-mail-password" not in str(caught.value)


def test_the_public_url_must_be_http_and_loses_a_trailing_slash(
    api_env: pytest.MonkeyPatch,
) -> None:
    api_env.setenv("APP_PUBLIC_URL", "https://app.example.test/")
    assert load_api_settings(env_file=None).app_public_url == "https://app.example.test"

    api_env.setenv("APP_PUBLIC_URL", "app.example.test")
    with pytest.raises(ValidationError, match="app_public_url"):
        load_api_settings(env_file=None)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MAIL_BACKEND", "carrier-pigeon"),
        ("MAIL_SMTP_PORT", "0"),
        ("PASSWORD_RESET_PER_EMAIL_PER_HOUR", "0"),
    ],
)
def test_rejects_a_bad_mail_or_reset_value_naming_the_key(
    api_env: pytest.MonkeyPatch, key: str, value: str
) -> None:
    api_env.setenv(key, value)
    with pytest.raises(ValidationError, match=key.lower()):
        load_api_settings(env_file=None)


def test_email_code_settings_default_to_10_minutes_5_tries_60_seconds_5_an_hour_and_250_a_day(
    api_env: pytest.MonkeyPatch,
) -> None:
    settings = load_api_settings(env_file=None)

    assert settings.email_code_ttl_minutes == 10
    assert settings.email_code_max_wrong_tries == 5
    assert settings.email_code_resend_seconds == 60
    assert settings.email_code_per_user_per_hour == 5
    assert settings.mail_daily_limit == 250


def test_email_code_settings_are_read_from_the_environment(api_env: pytest.MonkeyPatch) -> None:
    for key, value in zip(EMAIL_CODE_KEYS, ("15", "3", "30", "4", "100"), strict=True):
        api_env.setenv(key, value)

    settings = load_api_settings(env_file=None)

    assert (
        settings.email_code_ttl_minutes,
        settings.email_code_max_wrong_tries,
        settings.email_code_resend_seconds,
        settings.email_code_per_user_per_hour,
        settings.mail_daily_limit,
    ) == (15, 3, 30, 4, 100)


@pytest.mark.parametrize("key", EMAIL_CODE_KEYS)
def test_rejects_an_email_code_limit_of_zero_naming_the_key(
    api_env: pytest.MonkeyPatch, key: str
) -> None:
    api_env.setenv(key, "0")
    with pytest.raises(ValidationError, match=key.lower()):
        load_api_settings(env_file=None)


def test_google_sign_in_is_off_by_default_with_googles_urls(api_env: pytest.MonkeyPatch) -> None:
    settings = load_api_settings(env_file=None)

    assert not settings.google_enabled
    assert settings.google_auth_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert settings.google_token_url == "https://oauth2.googleapis.com/token"  # noqa: S105
    assert settings.google_jwks_url == "https://www.googleapis.com/oauth2/v3/certs"
    assert settings.google_issuer_list == ("https://accounts.google.com", "accounts.google.com")


def test_google_sign_in_is_on_with_both_keys_and_hides_the_secret(
    api_env: pytest.MonkeyPatch,
) -> None:
    for key, value in GOOGLE_ENV.items():
        api_env.setenv(key, value)
    api_env.setenv("GOOGLE_ISSUERS", " https://issuer.example.test , ")

    settings = load_api_settings(env_file=None)

    assert settings.google_enabled
    assert settings.google_client_secret is not None
    assert settings.google_client_secret.get_secret_value() == "made-up-google-client-secret"
    assert "made-up-google-client-secret" not in repr(settings)
    assert settings.google_issuer_list == ("https://issuer.example.test",)


@pytest.mark.parametrize("key", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"])
def test_google_with_only_one_of_its_keys_fails_naming_the_other(
    api_env: pytest.MonkeyPatch, key: str
) -> None:
    for name, value in GOOGLE_ENV.items():
        api_env.setenv(name, value)
    api_env.setenv(key, "")

    with pytest.raises(ValidationError, match=key) as caught:
        load_api_settings(env_file=None)
    assert "made-up-google-client-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("GOOGLE_TOKEN_URL", "http://oauth2.example.test/token", "google_token_url"),
        ("GOOGLE_JWKS_URL", "www.example.test/certs", "google_jwks_url"),
        ("GOOGLE_ISSUERS", " , ", "GOOGLE_ISSUERS"),
    ],
)
def test_rejects_a_google_url_without_https_or_no_issuer(
    api_env: pytest.MonkeyPatch, key: str, value: str, match: str
) -> None:
    api_env.setenv(key, value)
    with pytest.raises(ValidationError, match=match):
        load_api_settings(env_file=None)
