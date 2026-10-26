"""Request and response bodies of the auth and account routes.

A response never carries the password or its hash.
"""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from src.api.passwords import MAX_PASSWORD_CHARACTERS, MIN_PASSWORD_CHARACTERS
from src.db.models import UserRole

MAX_ACCOUNT_NAME_CHARACTERS = 200  # the length of `accounts.name`
MAX_EMAIL_CHARACTERS = 320  # the length of `users.email`
# Far above any sign-up password, so it only stops a huge body from being hashed at login.
MAX_LOGIN_PASSWORD_CHARACTERS = 1024


def normalize_email(value: str) -> str:
    """Return `value` trimmed and lowercased, or raise ValueError if it is not shaped like an email.

    Only the shape is checked (one `@`, text on both sides, no whitespace); whether the address
    exists is not.
    """
    email = value.strip().lower()
    local, at, domain = email.partition("@")
    if not at or not local or not domain or "@" in domain or any(c.isspace() for c in email):
        raise ValueError("not a valid email address")
    if len(email) > MAX_EMAIL_CHARACTERS:
        raise ValueError(f"an email address has at most {MAX_EMAIL_CHARACTERS} characters")
    return email


def normalize_account_name(value: str) -> str:
    """Return `value` trimmed, or raise ValueError if nothing is left."""
    name = value.strip()
    if not name:
        raise ValueError("the account name is empty")
    return name


AccountName = Annotated[
    str, Field(max_length=MAX_ACCOUNT_NAME_CHARACTERS), AfterValidator(normalize_account_name)
]
Email = Annotated[str, AfterValidator(normalize_email)]
# The password is not trimmed: every character the user typed counts.
Password = Annotated[
    str, Field(min_length=MIN_PASSWORD_CHARACTERS, max_length=MAX_PASSWORD_CHARACTERS)
]


class SignupRequest(BaseModel):
    """A new account and its first user."""

    model_config = ConfigDict(extra="forbid")

    account_name: AccountName
    email: Email
    password: Password


class LoginRequest(BaseModel):
    """Email and password. The password length is not checked here, so a wrong length is a 401.

    The login route refuses a password over `MAX_LOGIN_PASSWORD_CHARACTERS` with that same 401.
    """

    model_config = ConfigDict(extra="forbid")

    email: Email
    password: str


class PasswordChangeRequest(BaseModel):
    """The current password and a new one. The account and user come from the login, never from
    the body.

    The current password's length is not checked here, so a wrong length is the same 400 as a
    wrong password; the route refuses one over `MAX_LOGIN_PASSWORD_CHARACTERS` without hashing.
    """

    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: Password


class UserResponse(BaseModel):
    """A user and the account they belong to."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    email: str
    role: UserRole
    account_id: int
    account_name: str
