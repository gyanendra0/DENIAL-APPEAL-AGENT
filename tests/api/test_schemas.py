"""The request bodies: email and account name normalised, password length checked."""

import pytest
from pydantic import ValidationError

from src.api.schemas import (
    MAX_ACCOUNT_NAME_CHARACTERS,
    MAX_EMAIL_CHARACTERS,
    LoginRequest,
    SignupRequest,
    normalize_email,
)

PASSWORD = "twelve-chars"  # noqa: S105  (made up)
SHORT_PASSWORD = "short"  # noqa: S105  (made up)


def test_email_is_trimmed_and_lowercased() -> None:
    assert normalize_email("  Ann@Example.ORG\t") == "ann@example.org"


@pytest.mark.parametrize(
    "value",
    ["", "   ", "no-at-sign", "@example.org", "ann@", "ann@x@example.org", "an n@example.org"],
)
def test_rejects_a_value_not_shaped_like_an_email(value: str) -> None:
    with pytest.raises(ValueError, match="not a valid email"):
        normalize_email(value)


def test_rejects_an_email_longer_than_the_column() -> None:
    too_long = "a" * (MAX_EMAIL_CHARACTERS - len("@x.org") + 1) + "@x.org"
    with pytest.raises(ValueError, match="at most"):
        normalize_email(too_long)


def test_accepts_an_email_of_exactly_the_column_length() -> None:
    longest = "a" * (MAX_EMAIL_CHARACTERS - len("@x.org")) + "@x.org"
    assert normalize_email(longest) == longest


def test_signup_trims_the_account_name_and_normalises_the_email() -> None:
    body = SignupRequest(account_name="  Clinic One ", email=" A@X.org", password=PASSWORD)
    assert (body.account_name, body.email) == ("Clinic One", "a@x.org")


@pytest.mark.parametrize("name", ["", "   "])
def test_signup_refuses_an_empty_account_name(name: str) -> None:
    with pytest.raises(ValidationError, match="account_name"):
        SignupRequest(account_name=name, email="a@x.org", password=PASSWORD)


def test_signup_refuses_an_account_name_longer_than_the_column() -> None:
    with pytest.raises(ValidationError, match="account_name"):
        SignupRequest(
            account_name="n" * (MAX_ACCOUNT_NAME_CHARACTERS + 1), email="a@x.org", password=PASSWORD
        )


@pytest.mark.parametrize("length", [11, 129])
def test_signup_refuses_a_password_outside_12_to_128(length: int) -> None:
    with pytest.raises(ValidationError, match="password"):
        SignupRequest(account_name="c", email="a@x.org", password="p" * length)


@pytest.mark.parametrize("length", [12, 128])
def test_signup_accepts_a_password_of_12_or_128(length: int) -> None:
    body = SignupRequest(account_name="c", email="a@x.org", password="p" * length)
    assert len(body.password) == length


def test_signup_keeps_spaces_in_the_password() -> None:
    body = SignupRequest(account_name="c", email="a@x.org", password=f" {PASSWORD} ")
    assert body.password == f" {PASSWORD} "


def test_signup_refuses_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="role"):
        SignupRequest.model_validate(
            {"account_name": "c", "email": "a@x.org", "password": PASSWORD, "role": "admin"}
        )


def test_login_accepts_any_password_length() -> None:
    assert LoginRequest(email="A@x.org", password=SHORT_PASSWORD).email == "a@x.org"
