from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from src.config.settings import get_settings, load_llm_gateway_settings, load_settings

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


@pytest.mark.parametrize("key", ["LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL"])
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

    assert {*REQUIRED_GATEWAY_ENV, *OPTIONAL_GATEWAY_KEYS, "PROMPT_DIR"} <= listed
    assert settings.has_fallback is True
    assert settings.llm_fallback_api_key is not None
    # Worked out before the asserts, so a failure never prints the value of a real key.
    primary_is_placeholder = _is_placeholder(settings.llm_primary_api_key)
    fallback_is_placeholder = _is_placeholder(settings.llm_fallback_api_key)
    assert primary_is_placeholder, "LLM_PRIMARY_API_KEY in .env.example is not a placeholder"
    assert fallback_is_placeholder, "LLM_FALLBACK_API_KEY in .env.example is not a placeholder"


def _is_placeholder(key: SecretStr) -> bool:
    return key.get_secret_value().startswith("replace-with-")
