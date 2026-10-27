"""Sign-up: a new account and its first user, an admin, written together or not at all.

An email held by a user who never confirmed it does not block a sign-up: that user's account is
brand new (it never logged in, and no route adds users to an account), so it is deleted and the
new sign-up takes the email.
"""

import logging
from datetime import datetime

import psycopg.errors
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.login import find_user_by_email
from src.api.passwords import hash_password
from src.db.models import Account, User, UserRole

logger = logging.getLogger(__name__)

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


def remove_unverified_holder(session: Session, email: str) -> None:
    """Delete the account of the user holding `email` if that user never confirmed it.

    `email` must already be trimmed and lowercased. Deleting the account removes the user, the
    user's codes and the `account_created` audit row with it (every link cascades). A verified
    holder is left alone; the unique key then refuses the sign-up. The holder's row is locked and
    read again before the delete, so a code confirmed at the same moment keeps its account.
    """
    holder = find_user_by_email(session, email)
    if holder is None or holder.email_verified_at is not None:
        return
    account_id = holder.account_id
    locked = session.scalars(
        select(User)
        .where(User.account_id == account_id, User.id == holder.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if locked is None or locked.email_verified_at is not None:
        return
    session.expunge(holder)
    session.execute(delete(Account).where(Account.id == account_id))
    logger.info("sign-up: replaced unverified account %d", account_id)


def create_account_with_admin(
    session: Session,
    *,
    account_name: str,
    email: str,
    password: str | None,
    verified_at: datetime | None,
) -> User:
    """Add an account named `account_name` and its `admin` user, and return the user.

    `email` must already be trimmed and lowercased. `password` is None for a user who signs in
    with Google only (no password yet). `verified_at` is when the email was confirmed, or None
    until the user types the emailed code. An unverified holder of `email` is replaced first
    (`remove_unverified_holder`). Both rows are written inside one savepoint, together with that
    replacement: a taken name or email rolls all of it back and raises `AccountNameTakenError` or
    `EmailTakenError`. Any other database error propagates. Hashing takes tens of milliseconds on
    purpose.
    """
    password_hash = hash_password(password) if password is not None else None
    try:
        with session.begin_nested():
            remove_unverified_holder(session, email)
            account = Account(name=account_name)
            session.add(account)
            session.flush()
            user = User(
                account_id=account.id,
                email=email,
                password_hash=password_hash,
                role=UserRole.ADMIN,
                email_verified_at=verified_at,
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
