"""The users of the logged-in admin's own account.

The `account_id` comes from the logged-in user, never from the path: a user id of another account
is answered with 404, the same as an id that does not exist.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.api.dependencies import CurrentUser, get_db_session, require_role
from src.api.schemas import UserResponse
from src.api.user_lookup import find_user_response
from src.db.models import UserRole

router = APIRouter(prefix="/users", tags=["users"])

USER_NOT_FOUND = "user not found"


@router.get("/{user_id}")
def get_user(
    user_id: int,
    admin: Annotated[CurrentUser, Depends(require_role(UserRole.ADMIN))],
    session: Annotated[Session, Depends(get_db_session)],
) -> UserResponse:
    """Return one user of the admin's account, or answer 404."""
    found = find_user_response(session, account_id=admin.account_id, user_id=user_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, USER_NOT_FOUND)
    return found
