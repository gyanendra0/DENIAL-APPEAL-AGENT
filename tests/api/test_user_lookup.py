"""One user of one account, with the account name and the role the user acts with; another
account's user is not found."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.api.user_lookup import find_user_response
from src.db.models import UserRole
from tests.api.conftest import add_member, api_settings

# The users made here count as having confirmed their email.
VERIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
SETTINGS = api_settings()


def test_returns_the_user_with_the_account_name(session: Session) -> None:
    user = add_member(
        session, account_name="One", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    found = find_user_response(
        session, settings=SETTINGS, account_id=user.account_id, user_id=user.id
    )
    assert found is not None
    assert found.model_dump() == {
        "user_id": user.id,
        "email": "a@x.org",
        "role": UserRole.REVIEWER,
        "account_id": user.account_id,
        "account_name": "One",
    }


def test_reports_admin_for_an_email_in_admin_emails_not_the_stored_role(session: Session) -> None:
    user = add_member(
        session, account_name="One", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    listed = SETTINGS.model_copy(update={"admin_emails": "a@x.org"})
    found = find_user_response(
        session, settings=listed, account_id=user.account_id, user_id=user.id
    )
    assert found is not None and found.role == UserRole.ADMIN
    assert user.role == UserRole.REVIEWER


def test_user_of_another_account_is_not_found(session: Session) -> None:
    a = add_member(
        session, account_name="A", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    b = add_member(
        session, account_name="B", email="b@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    found = find_user_response(session, settings=SETTINGS, account_id=a.account_id, user_id=b.id)
    assert found is None


def test_unknown_user_is_not_found(session: Session) -> None:
    a = add_member(
        session, account_name="A", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    found = find_user_response(
        session, settings=SETTINGS, account_id=a.account_id, user_id=a.id + 1000
    )
    assert found is None
