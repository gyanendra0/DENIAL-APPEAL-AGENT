"""One user of one account, with the account name; another account's user is not found."""

from sqlalchemy.orm import Session

from src.api.signup import create_account_with_admin
from src.api.user_lookup import find_user_response
from src.db.models import UserRole

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)


def test_returns_the_user_with_the_account_name(session: Session) -> None:
    user = create_account_with_admin(
        session, account_name="One", email="a@x.org", password=PASSWORD
    )
    found = find_user_response(session, account_id=user.account_id, user_id=user.id)
    assert found is not None
    assert found.model_dump() == {
        "user_id": user.id,
        "email": "a@x.org",
        "role": UserRole.ADMIN,
        "account_id": user.account_id,
        "account_name": "One",
    }


def test_user_of_another_account_is_not_found(session: Session) -> None:
    a = create_account_with_admin(session, account_name="A", email="a@x.org", password=PASSWORD)
    b = create_account_with_admin(session, account_name="B", email="b@x.org", password=PASSWORD)
    assert find_user_response(session, account_id=a.account_id, user_id=b.id) is None


def test_unknown_user_is_not_found(session: Session) -> None:
    a = create_account_with_admin(session, account_name="A", email="a@x.org", password=PASSWORD)
    assert find_user_response(session, account_id=a.account_id, user_id=a.id + 1000) is None
