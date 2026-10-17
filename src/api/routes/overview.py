"""The account's overview: jobs, drafts, costs and times. Any role; the account comes from the
logged-in user."""

from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser, DbSession, current_user
from src.review.overview import Overview, read_overview

router = APIRouter(tags=["overview"])


@router.get("/overview")
def get_overview(
    user: Annotated[CurrentUser, Depends(current_user)], session: DbSession
) -> Overview:
    """Return the account's job and draft counts, what the jobs cost and how long they took."""
    return read_overview(session, user.account_id)
