"""The logged-in user's own record."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import NOT_LOGGED_IN, CurrentUser, DbSession, current_user
from src.api.schemas import UserResponse
from src.api.user_lookup import find_user_response

router = APIRouter(tags=["users"])


@router.get("/me")
def me(
    user: Annotated[CurrentUser, Depends(current_user)],
    session: DbSession,
) -> UserResponse:
    """Return the logged-in user and their account."""
    found = find_user_response(session, account_id=user.account_id, user_id=user.user_id)
    if found is None:
        # Deleted between the login check and this read.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_LOGGED_IN)
    return found
