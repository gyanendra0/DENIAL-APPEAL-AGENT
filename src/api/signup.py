"""Sign-up: a new user joins the one shared account named by `SHARED_ACCOUNT_NAME`, or an
individual gets a personal account of their own.

The settings decide which (`signup_kind`): an allowed email joins the shared account; any other
email gets a personal account while `INDIVIDUAL_SIGNUP_ENABLED` is on, and is refused otherwise.
The first sign-up creates the shared account; later ones join it. An email held by a user who
never confirmed it does not block a sign-up: that user never logged in, so has no uploads or
reviews, and is deleted so the new sign-up can take the email (one alone in its own personal or
older account goes with that account). Nothing is audited until the email is confirmed
(`record_joined`).
"""

import logging
import secrets
from datetime import datetime

import psycopg.errors
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.login import find_user_by_email
from src.api.passwords import hash_password
from src.config.settings import ApiSettings
from src.db.models import Account, AccountKind, AuditAction, AuditEvent, User, UserRole
from src.db.query import for_account
from src.review.audit import record_audit_event

logger = logging.getLogger(__name__)

# The database's unique key decides whether an email is taken, so sign-up never has to search
# other accounts' rows.
USER_EMAIL_KEY = "uq_users_email"
# A personal account's name is never shown; it only has to be unique (`accounts.name`).
PERSONAL_ACCOUNT_PREFIX = "personal-"
PERSONAL_ACCOUNT_NAME_BYTES = 8


class EmailTakenError(Exception):
    """A user of some account already has this email."""


def _taken_key(error: IntegrityError) -> str | None:
    if isinstance(error.orig, psycopg.errors.UniqueViolation):
        return error.orig.diag.constraint_name
    return None


def signup_kind(settings: ApiSettings, email: str) -> AccountKind | None:
    """The kind of account `email` (trimmed and lowercased) signs up into, or None if it may not
    sign up. An admin, an allowed email or an email under an allowed domain joins the shared
    account (both allow-lists empty: only the admins); any other email gets a personal account
    while individual sign-up is enabled."""
    if email in settings.admin_email_set or email in settings.signup_allowed_email_set:
        return AccountKind.SHARED
    if email.rpartition("@")[2] in settings.signup_allowed_domain_set:
        return AccountKind.SHARED
    return AccountKind.PERSONAL if settings.individual_signup_enabled else None


def shared_account_id(session: Session, name: str) -> int:
    """Return the id of the account named `name`, adding it first if there is none.

    The unique key on `accounts.name` makes two sign-ups at the same moment safe: the second
    insert does nothing and both read the same row.
    """
    session.execute(
        insert(Account).values(name=name).on_conflict_do_nothing(index_elements=["name"])
    )
    return session.scalars(select(Account.id).where(Account.name == name)).one()


def personal_account_id(session: Session) -> int:
    """Add a new personal account with a random name and return its id."""
    name = PERSONAL_ACCOUNT_PREFIX + secrets.token_hex(PERSONAL_ACCOUNT_NAME_BYTES)
    account = Account(name=name, kind=AccountKind.PERSONAL)
    session.add(account)
    session.flush()
    return account.id


def remove_unverified_holder(session: Session, email: str, shared_name: str) -> None:
    """Delete the user holding `email` if that user never confirmed it.

    `email` must already be trimmed and lowercased. Usually only the user goes (with the user's
    codes and Google link, which cascade); the account and its other users stay. A holder alone
    in an account other than the shared one (`shared_name`) is an individual in their personal
    account, or was made before the shared account, when sign-up gave each user an account and
    wrote its `account_created` row at once; that account goes instead, and the row with it
    (every link cascades). A verified holder is left
    alone; the unique key then refuses the sign-up. The holder's row is locked and read again
    before the delete, so a code confirmed at the same moment keeps its user.
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
    if _alone_in_older_account(session, account_id, user_id, shared_name):
        session.execute(delete(Account).where(Account.id == account_id))
        logger.info("sign-up: replaced unverified account %d", account_id)
        return
    session.execute(delete(User).where(User.account_id == account_id, User.id == user_id))
    logger.info("sign-up: replaced unverified user %d of account %d", user_id, account_id)


def _alone_in_older_account(
    session: Session, account_id: int, user_id: int, shared_name: str
) -> bool:
    # Not "kind is personal": accounts made before the shared account are `shared` too.
    name = session.scalars(select(Account.name).where(Account.id == account_id)).one()
    other = session.scalars(for_account(User, account_id).where(User.id != user_id).limit(1))
    return name != shared_name and other.first() is None


def create_member(
    session: Session,
    *,
    settings: ApiSettings,
    kind: AccountKind,
    email: str,
    password: str | None,
    verified_at: datetime | None,
) -> User:
    """Add a user with `email` to the shared account, or to a new personal account of their
    own, as `kind` says, and return the user.

    `email` must already be trimmed and lowercased, and `kind` the one `signup_kind` gave it; the
    caller checks. The user is stored as `reviewer`; `effective_role` decides who acts as admin.
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
            remove_unverified_holder(session, email, settings.shared_account_name)
            if kind is AccountKind.PERSONAL:
                account_id = personal_account_id(session)
            else:
                account_id = shared_account_id(session, settings.shared_account_name)
            user = User(
                account_id=account_id,
                email=email,
                password_hash=password_hash,
                # `ADMIN_EMAILS` or a personal account makes an admin on each request
                # (`src/api/roles.py`).
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
