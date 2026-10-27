"""Who a checked Google identity opens: an already linked user, a user to link, or nobody yet.

The user is found only through the verified email (`find_user_by_email`, the one allowed lookup
without `account_id`); the user's identities are then read inside that user's account. Whether a
Google account is linked to some other user is never searched for: the unique key
`uq_user_identities_provider_subject` refuses the insert instead, as sign-up does with emails.
"""

import secrets
from enum import StrEnum

import psycopg.errors
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.google_oidc import GoogleIdentity
from src.api.login import find_user_by_email
from src.api.signup import create_account_with_admin
from src.db.models import AuditAction, IdentityProvider, User, UserIdentity
from src.db.query import for_account
from src.review.audit import record_audit_event

PROVIDER_SUBJECT_KEY = "uq_user_identities_provider_subject"
# Google is the authority for these addresses, so a verified one is the person's own today.
GOOGLE_MAIL_DOMAIN = "gmail.com"


class GoogleRefusal(StrEnum):
    """Why a Google sign-in is refused. The value is the code the login screen is sent."""

    FAILED = "failed"
    NOT_VERIFIED = "not_verified"
    USE_PASSWORD = "use_password"  # noqa: S105  (a code, not a password)
    USE_SIGNUP = "use_signup"
    LINKED_ELSEWHERE = "linked_elsewhere"
    EXPIRED = "expired"


class GoogleRefusedError(Exception):
    """The Google identity may not open a user; `reason` says why."""

    def __init__(self, reason: GoogleRefusal) -> None:
        super().__init__(reason.value)
        self.reason = reason


def may_link(identity: GoogleIdentity) -> bool:
    """True if Google vouches for the email today: verified, and a Gmail address or a Google
    Workspace account (`hd`), whose domain Google manages.

    A verified non-Gmail address on a plain Google account was checked once, but whoever controls
    that address now could have made the Google account, so it never links to an existing user.
    """
    if not identity.email_verified:
        return False
    domain = identity.email.rpartition("@")[2]
    return domain == GOOGLE_MAIL_DOMAIN or identity.hosted_domain is not None


def _is_provider_subject_taken(error: IntegrityError) -> bool:
    return (
        isinstance(error.orig, psycopg.errors.UniqueViolation)
        and error.orig.diag.constraint_name == PROVIDER_SUBJECT_KEY
    )


def _add_identity(session: Session, user: User, identity: GoogleIdentity) -> None:
    """Link `identity` to `user` inside a savepoint; a Google account linked to another user
    raises `GoogleRefusedError(LINKED_ELSEWHERE)`."""
    try:
        with session.begin_nested():
            session.add(
                UserIdentity(
                    account_id=user.account_id,
                    user_id=user.id,
                    provider=IdentityProvider.GOOGLE,
                    subject=identity.subject,
                    email_at_link=identity.email,
                )
            )
            session.flush()
    except IntegrityError as error:
        if _is_provider_subject_taken(error):
            raise GoogleRefusedError(GoogleRefusal.LINKED_ELSEWHERE) from None
        # Most likely the user got a Google identity in a request running at the same time.
        raise GoogleRefusedError(GoogleRefusal.FAILED) from None


def sign_in_with_google(session: Session, identity: GoogleIdentity) -> User | None:
    """Return the user `identity` opens, linking it first if allowed, or None if no user has
    its email and Google vouches for it (the caller then offers a new account). Does not commit.

    Refuses with `GoogleRefusedError`: an unverified email; a user already linked to another
    Google account; an email Google does not vouch for (`may_link`), for a link or a new
    account; a Google account linked to another user. A new link writes a `google_linked`
    audit event in the same transaction.
    """
    if not identity.email_verified:
        raise GoogleRefusedError(GoogleRefusal.NOT_VERIFIED)
    user = find_user_by_email(session, identity.email)
    if user is None:
        # A new account would hold the email before its owner signs up, and keep the Google
        # login after they reset the password; so it needs an email Google vouches for too.
        if not may_link(identity):
            raise GoogleRefusedError(GoogleRefusal.USE_SIGNUP)
        return None
    linked = session.scalars(
        for_account(UserIdentity, user.account_id).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == IdentityProvider.GOOGLE
        )
    ).one_or_none()
    if linked is not None:
        if secrets.compare_digest(linked.subject.encode(), identity.subject.encode()):
            return user
        # Another Google account has this email now; only the password opens the user.
        raise GoogleRefusedError(GoogleRefusal.USE_PASSWORD)
    if not may_link(identity):
        raise GoogleRefusedError(GoogleRefusal.USE_PASSWORD)
    _add_identity(session, user, identity)
    record_audit_event(session, user.account_id, user.id, AuditAction.GOOGLE_LINKED, None)
    return user


def create_google_account(session: Session, *, account_name: str, subject: str, email: str) -> User:
    """Add an account, its `admin` user (no password) and the user's Google identity, and
    return the user. Does not commit; does not write the audit event.

    All three rows are written inside one savepoint, so a refusal leaves none of them. Raises
    `AccountNameTakenError` or `EmailTakenError` like sign-up, and
    `GoogleRefusedError(LINKED_ELSEWHERE)` if the Google account opens another user.
    """
    identity = GoogleIdentity(subject=subject, email=email, email_verified=True, hosted_domain=None)
    with session.begin_nested():
        user = create_account_with_admin(
            session, account_name=account_name, email=email, password=None
        )
        _add_identity(session, user, identity)
    return user
