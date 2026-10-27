"""Sign-up writes the account and its admin together, or nothing."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.passwords import verify_password
from src.api.signup import AccountNameTakenError, EmailTakenError, create_account_with_admin
from src.db.models import Account, User, UserRole

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)


def _counts(session: Session) -> tuple[int, int]:
    accounts = session.scalar(select(func.count()).select_from(Account))
    users = session.scalar(select(func.count()).select_from(User))
    assert accounts is not None and users is not None
    return accounts, users


def test_creates_the_account_and_an_admin_with_a_hashed_password(session: Session) -> None:
    user = create_account_with_admin(
        session, account_name="Clinic One", email="ann@x.org", password=PASSWORD
    )
    account = session.get(Account, user.account_id)
    assert account is not None and account.name == "Clinic One"
    assert (user.email, user.role) == ("ann@x.org", UserRole.ADMIN)
    assert user.password_hash is not None and user.password_hash != PASSWORD
    assert verify_password(user.password_hash, PASSWORD)


def test_taken_account_name_raises_and_writes_nothing(session: Session) -> None:
    create_account_with_admin(session, account_name="Clinic", email="a@x.org", password=PASSWORD)
    before = _counts(session)
    with pytest.raises(AccountNameTakenError):
        create_account_with_admin(
            session, account_name="Clinic", email="b@x.org", password=PASSWORD
        )
    assert _counts(session) == before


def test_taken_email_raises_and_writes_no_account(session: Session) -> None:
    create_account_with_admin(session, account_name="One", email="a@x.org", password=PASSWORD)
    before = _counts(session)
    with pytest.raises(EmailTakenError) as caught:
        create_account_with_admin(session, account_name="Two", email="a@x.org", password=PASSWORD)
    assert _counts(session) == before
    assert "a@x.org" not in str(caught.value)


def test_other_database_errors_propagate(session: Session) -> None:
    # An email that is not lowercased breaks `ck_users_email_normalized`, not a unique key.
    with pytest.raises(IntegrityError):
        create_account_with_admin(session, account_name="One", email="A@x.org", password=PASSWORD)
