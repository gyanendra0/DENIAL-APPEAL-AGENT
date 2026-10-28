"""Request and response bodies of the auth and account routes.

A response never carries the password or its hash.
"""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from src.api.passwords import MAX_PASSWORD_CHARACTERS, MIN_PASSWORD_CHARACTERS
from src.db.models import AccountKind, UserRole

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


Email = Annotated[str, AfterValidator(normalize_email)]
# The password is not trimmed: every character the user typed counts.
Password = Annotated[
    str, Field(min_length=MIN_PASSWORD_CHARACTERS, max_length=MAX_PASSWORD_CHARACTERS)
]


class SignupRequest(BaseModel):
    """A new user of the shared account. The account comes from the settings, never the body."""

    model_config = ConfigDict(extra="forbid")

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


class PasswordResetRequest(BaseModel):
    """The email a reset code is asked for. The answer never says whether it has an account."""

    model_config = ConfigDict(extra="forbid")

    email: Email


class PasswordResetConfirm(BaseModel):
    """The email, the 6-digit code emailed to it and the new password."""

    model_config = ConfigDict(extra="forbid")

    email: Email
    code: Annotated[str, Field(pattern=r"^[0-9]{6}$")]
    new_password: Password


class PasswordResetAnswer(BaseModel):
    """The one answer to every reset request."""

    model_config = ConfigDict(frozen=True)

    detail: str


class VerifyEmailRequest(BaseModel):
    """The 6-digit code from the sign-up email. The account and the user come from the signed
    `email_verify` cookie, never from the body."""

    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class EmailNotVerifiedAnswer(BaseModel):
    """The answer to a login with the right password whose email is not confirmed yet."""

    model_config = ConfigDict(frozen=True)

    detail: str
    code: str


class CodeSentAnswer(BaseModel):
    """The answer when a new code is on its way."""

    model_config = ConfigDict(frozen=True)

    detail: str


class AuthOptions(BaseModel):
    """The ways in besides email and password that this server offers."""

    model_config = ConfigDict(frozen=True)

    google: bool


class GoogleSignupInfo(BaseModel):
    """The verified Google email a pending Google sign-up will create its user with, and the
    kind of account the user will join: the shared account (with its name) or a new personal
    account (no name: its made-up name is never shown)."""

    model_config = ConfigDict(frozen=True)

    email: str
    account_kind: AccountKind
    account_name: str | None


class GoogleSignupRequest(BaseModel):
    """The deliberate second click that confirms a Google sign-up. Empty: the email and the
    Google account come from the signed `google_signup` cookie, the account from the
    settings."""

    model_config = ConfigDict(extra="forbid")


class UserResponse(BaseModel):
    """A user and the account they belong to. A personal account's name is made up; a screen
    shows its kind instead."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    email: str
    role: UserRole
    account_id: int
    account_name: str
    account_kind: AccountKind
