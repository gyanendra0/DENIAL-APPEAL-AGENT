"""Read one user of one account, with the account's name, for a response."""

from sqlalchemy.orm import Session

from src.api.roles import effective_role
from src.api.schemas import UserResponse
from src.config.settings import ApiSettings
from src.db.models import Account, User
from src.db.query import for_account


def find_user_response(
    session: Session, *, settings: ApiSettings, account_id: int, user_id: int
) -> UserResponse | None:
    """Return user `user_id` of account `account_id` with the account's name and kind, or None
    if that account has no such user.

    The role is the one the user acts with (`effective_role`), not the stored one.
    """
    row = session.execute(
        for_account(User, account_id)
        .where(User.id == user_id)
        .join(Account, Account.id == User.account_id)
        .add_columns(Account.name, Account.kind)
    ).one_or_none()
    if row is None:
        return None
    user, account_name, account_kind = row
    return UserResponse(
        user_id=user.id,
        email=user.email,
        role=effective_role(settings, user.email, user.role, account_kind),
        account_id=user.account_id,
        account_name=account_name,
        account_kind=account_kind,
    )
