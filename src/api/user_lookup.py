"""Read one user of one account, with the account's name, for a response."""

from sqlalchemy.orm import Session

from src.api.schemas import UserResponse
from src.db.models import Account, User
from src.db.query import for_account


def find_user_response(session: Session, *, account_id: int, user_id: int) -> UserResponse | None:
    """Return user `user_id` of account `account_id`, or None if that account has no such user."""
    row = session.execute(
        for_account(User, account_id)
        .where(User.id == user_id)
        .join(Account, Account.id == User.account_id)
        .add_columns(Account.name)
    ).one_or_none()
    if row is None:
        return None
    user, account_name = row
    return UserResponse(
        user_id=user.id,
        email=user.email,
        role=user.role,
        account_id=user.account_id,
        account_name=account_name,
    )
