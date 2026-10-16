"""Sign-up: a new account and its first user, an admin, written together or not at all."""

import psycopg.errors
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.passwords import hash_password
from src.db.models import Account, User, UserRole

# The database's unique keys decide whether a name or an email is taken, so sign-up never has to
# search other accounts' rows. `accounts_name_key` is the name Postgres gave in migration 0001.
ACCOUNT_NAME_KEY = "accounts_name_key"
USER_EMAIL_KEY = "uq_users_email"


class AccountNameTakenError(Exception):
    """Another account already has this name."""


class EmailTakenError(Exception):
    """A user of some account already has this email."""


def _taken_key(error: IntegrityError) -> str | None:
    if isinstance(error.orig, psycopg.errors.UniqueViolation):
        return error.orig.diag.constraint_name
    return None


def create_account_with_admin(
    session: Session, *, account_name: str, email: str, password: str
) -> User:
    """Add an account named `account_name` and its `admin` user, and return the user.

    `email` must already be trimmed and lowercased. Both rows are written inside one savepoint:
    a taken name or email rolls both back and raises `AccountNameTakenError` or `EmailTakenError`.
    Any other database error propagates. Hashing takes tens of milliseconds on purpose.
    """
    password_hash = hash_password(password)
    try:
        with session.begin_nested():
            account = Account(name=account_name)
            session.add(account)
            session.flush()
            user = User(
                account_id=account.id,
                email=email,
                password_hash=password_hash,
                role=UserRole.ADMIN,
            )
            session.add(user)
            session.flush()
    except IntegrityError as error:
        key = _taken_key(error)
        if key == ACCOUNT_NAME_KEY:
            raise AccountNameTakenError(account_name) from None
        if key == USER_EMAIL_KEY:
            # The email is personal data: it is not put in the message.
            raise EmailTakenError("the email is taken") from None
        raise
    return user
