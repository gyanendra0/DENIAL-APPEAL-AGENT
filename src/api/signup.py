"""Sign-up: a new user joins the one shared account named by `SHARED_ACCOUNT_NAME`.

Only an email the settings allow may sign up (`may_sign_up`). The first sign-up creates the
shared account; later ones join it. An email held by a user who never confirmed it does not
block a sign-up: that user never logged in, so has no uploads, reviews or audit events, and is
deleted so the new sign-up can take the email. Nothing is audited until the email is confirmed
(`record_joined`).
"""

import logging
from datetime import datetime

import psycopg.errors
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.login import find_user_by_email
from src.api.passwords import hash_password
from src.config.settings import ApiSettings
from src.db.models import Account, AuditAction, AuditEvent, User, UserRole
from src.db.query import for_account
from src.review.audit import record_audit_event

logger = logging.getLogger(__name__)

# The database's unique key decides whether an email is taken, so sign-up never has to search
# other accounts' rows.
USER_EMAIL_KEY = "uq_users_email"


class EmailTakenError(Exception):
    """A user of some account already has this email."""


def _taken_key(error: IntegrityError) -> str | None:
    if isinstance(error.orig, psycopg.errors.UniqueViolation):
        return error.orig.diag.constraint_name
    return None


def may_sign_up(settings: ApiSettings, email: str) -> bool:
    """True if `email` (trimmed and lowercased) may sign up: an admin, an allowed email, or an
    email under an allowed domain. Both allow-lists empty: only the admins."""
    if email in settings.admin_email_set or email in settings.signup_allowed_email_set:
        return True
    return email.rpartition("@")[2] in settings.signup_allowed_domain_set


def shared_account_id(session: Session, name: str) -> int:
    """Return the id of the account named `name`, adding it first if there is none.

    The unique key on `accounts.name` makes two sign-ups at the same moment safe: the second
    insert does nothing and both read the same row.
    """
    session.execute(
        insert(Account).values(name=name).on_conflict_do_nothing(index_elements=["name"])
    )
    return session.scalars(select(Account.id).where(Account.name == name)).one()


def remove_unverified_holder(session: Session, email: str) -> None:
    """Delete the user holding `email` if that user never confirmed it.

    `email` must already be trimmed and lowercased. Only the user goes (with the user's codes
    and Google link, which cascade); the account and its other users stay. A verified holder is
    left alone; the unique key then refuses the sign-up. The holder's row is locked and read
    again before the delete, so a code confirmed at the same moment keeps its user.
    """
    holder = find_user_by_email(session, email)
    if holder is None or holder.email_verified_at is not None:
        return
    account_id, user_id = holder.account_id, holder.id
    locked = session.scalars(
        for_account(User, account_id)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if locked is None or locked.email_verified_at is not None:
        return
    session.expunge(locked)
    session.execute(delete(User).where(User.account_id == account_id, User.id == user_id))
    logger.info("sign-up: replaced unverified user %d of account %d", user_id, account_id)


def create_member(
    session: Session,
    *,
    settings: ApiSettings,
    email: str,
    password: str | None,
    verified_at: datetime | None,
) -> User:
    """Add a user with `email` to the shared account and return the user.

    `email` must already be trimmed and lowercased and allowed (`may_sign_up`); the caller
    checks. The user is stored as `reviewer`; `ADMIN_EMAILS` decides who acts as admin.
    `password` is None for a user who signs in with Google only (no password yet).
    `verified_at` is when the email was confirmed, or None until the user types the emailed
    code. An unverified holder of `email` is replaced first (`remove_unverified_holder`). The
    user is written inside one savepoint, together with that replacement: a taken email rolls
    all of it back and raises `EmailTakenError`. Any other database error propagates. Hashing
    takes tens of milliseconds on purpose.
    """
    password_hash = hash_password(password) if password is not None else None
    try:
        with session.begin_nested():
            remove_unverified_holder(session, email)
            account_id = shared_account_id(session, settings.shared_account_name)
            user = User(
                account_id=account_id,
                email=email,
                password_hash=password_hash,
                # `ADMIN_EMAILS` makes an admin on each request (`src/api/roles.py`).
                role=UserRole.REVIEWER,
                email_verified_at=verified_at,
            )
            session.add(user)
            session.flush()
    except IntegrityError as error:
        if _taken_key(error) == USER_EMAIL_KEY:
            # The email is personal data: it is not put in the message.
            raise EmailTakenError("the email is taken") from None
        raise
    return user


def record_joined(session: Session, user: User) -> None:
    """Audit that `user`'s email is now confirmed: `account_created` for the account's first
    confirmed user, `user_joined` for every later one. Does not commit.

    The account row is locked first, so two users confirmed at the same moment do not both
    write `account_created`.
    """
    session.scalars(select(Account.id).where(Account.id == user.account_id).with_for_update()).one()
    created = session.scalars(
        for_account(AuditEvent, user.account_id)
        .where(AuditEvent.action == AuditAction.ACCOUNT_CREATED)
        .limit(1)
    ).first()
    action = AuditAction.ACCOUNT_CREATED if created is None else AuditAction.USER_JOINED
    record_audit_event(session, user.account_id, user.id, action, None)
