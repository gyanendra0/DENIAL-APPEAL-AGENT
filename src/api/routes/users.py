"""The users of the logged-in admin's own account.

The `account_id` comes from the logged-in user, never from the path: a user id of another account
is answered with 404, the same as an id that does not exist.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import CurrentUser, DbSession, get_api_settings, require_role
from src.api.schemas import UserResponse
from src.api.user_lookup import find_user_response
from src.config.settings import ApiSettings
from src.db.models import UserRole

router = APIRouter(prefix="/users", tags=["users"])

USER_NOT_FOUND = "user not found"


@router.get("/{user_id}")
def get_user(
    user_id: int,
    admin: Annotated[CurrentUser, Depends(require_role(UserRole.ADMIN))],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    session: DbSession,
) -> UserResponse:
    """Return one user of the admin's account, or answer 404. The role is the one the user
    acts with."""
    found = find_user_response(
        session, settings=settings, account_id=admin.account_id, user_id=user_id
    )
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, USER_NOT_FOUND)
    return found
