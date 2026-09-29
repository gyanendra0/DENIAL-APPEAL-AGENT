"""Account-scoped query helpers.

Every query on a business table starts here, so the `account_id` filter is never forgotten.
"""

from typing import TypeVar

from sqlalchemy import Select, select

from src.db.base import AccountScopedMixin

T = TypeVar("T", bound=AccountScopedMixin)


def for_account(model: type[T], account_id: int) -> Select[T]:
    """Return a SELECT on `model` already filtered to one account."""
    return select(model).where(model.account_id == account_id)
