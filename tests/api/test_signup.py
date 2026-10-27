"""Sign-up writes the account and its admin together, or nothing."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.email_codes import add_code
from src.api.passwords import verify_password
from src.api.signup import AccountNameTakenError, EmailTakenError, create_account_with_admin
from src.db.models import (
    Account,
    AuditAction,
    AuditEvent,
    EmailCode,
    EmailCodePurpose,
    User,
    UserRole,
)
from src.review.audit import record_audit_event

# The users made here count as having confirmed their email.
VERIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)


def _counts(session: Session) -> tuple[int, int]:
    accounts = session.scalar(select(func.count()).select_from(Account))
    users = session.scalar(select(func.count()).select_from(User))
    assert accounts is not None and users is not None
    return accounts, users


def test_creates_the_account_and_an_admin_with_a_hashed_password(session: Session) -> None:
    user = create_account_with_admin(
        session,
        account_name="Clinic One",
        email="ann@x.org",
        password=PASSWORD,
        verified_at=VERIFIED_AT,
    )
    account = session.get(Account, user.account_id)
    assert account is not None and account.name == "Clinic One"
    assert (user.email, user.role) == ("ann@x.org", UserRole.ADMIN)
    assert user.password_hash is not None and user.password_hash != PASSWORD
    assert verify_password(user.password_hash, PASSWORD)


def test_taken_account_name_raises_and_writes_nothing(session: Session) -> None:
    create_account_with_admin(
        session, account_name="Clinic", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    before = _counts(session)
    with pytest.raises(AccountNameTakenError):
        create_account_with_admin(
            session,
            account_name="Clinic",
            email="b@x.org",
            password=PASSWORD,
            verified_at=VERIFIED_AT,
        )
    assert _counts(session) == before


def test_taken_email_raises_and_writes_no_account(session: Session) -> None:
    create_account_with_admin(
        session, account_name="One", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    before = _counts(session)
    with pytest.raises(EmailTakenError) as caught:
        create_account_with_admin(
            session, account_name="Two", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
        )
    assert _counts(session) == before
    assert "a@x.org" not in str(caught.value)


def test_other_database_errors_propagate(session: Session) -> None:
    # An email that is not lowercased breaks `ck_users_email_normalized`, not a unique key.
    with pytest.raises(IntegrityError):
        create_account_with_admin(
            session, account_name="One", email="A@x.org", password=PASSWORD, verified_at=VERIFIED_AT
        )


def _unverified(session: Session, name: str = "Squatter", email: str = "a@x.org") -> User:
    user = create_account_with_admin(
        session, account_name=name, email=email, password=PASSWORD, verified_at=None
    )
    record_audit_event(session, user.account_id, user.id, AuditAction.ACCOUNT_CREATED, None)
    add_code(
        session,
        account_id=user.account_id,
        user_id=user.id,
        purpose=EmailCodePurpose.VERIFY_EMAIL,
        code="123456",
        secret="a-made-up-secret",  # noqa: S106  (made up)
        ttl_minutes=10,
        now=VERIFIED_AT,
    )
    return user


def test_a_new_user_is_unverified_unless_told_when_it_was_verified(session: Session) -> None:
    fresh = create_account_with_admin(
        session, account_name="One", email="a@x.org", password=PASSWORD, verified_at=None
    )
    vouched = create_account_with_admin(
        session, account_name="Two", email="b@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    assert (fresh.email_verified_at, vouched.email_verified_at) == (None, VERIFIED_AT)


def test_an_unverified_holder_of_the_email_is_replaced_with_everything_it_had(
    session: Session,
) -> None:
    squatter = _unverified(session)
    old_account_id = squatter.account_id
    user = create_account_with_admin(
        session, account_name="Owner", email="a@x.org", password=PASSWORD, verified_at=None
    )
    session.expire_all()
    assert session.get(Account, old_account_id) is None
    assert _counts(session) == (1, 1)
    assert session.scalars(select(User.account_id)).all() == [user.account_id]
    # The old account's audit row and code went with it.
    assert session.scalars(select(AuditEvent)).all() == []
    assert session.scalars(select(EmailCode)).all() == []


def test_a_verified_holder_of_the_email_is_not_replaced(session: Session) -> None:
    create_account_with_admin(
        session, account_name="Owner", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    with pytest.raises(EmailTakenError):
        create_account_with_admin(
            session, account_name="Other", email="a@x.org", password=PASSWORD, verified_at=None
        )
    assert _counts(session) == (1, 1)


def test_a_taken_name_keeps_the_unverified_holder(session: Session) -> None:
    _unverified(session)
    create_account_with_admin(
        session, account_name="Taken", email="b@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    with pytest.raises(AccountNameTakenError):
        create_account_with_admin(
            session, account_name="Taken", email="a@x.org", password=PASSWORD, verified_at=None
        )
    session.expire_all()
    assert sorted(session.scalars(select(Account.name))) == ["Squatter", "Taken"]
