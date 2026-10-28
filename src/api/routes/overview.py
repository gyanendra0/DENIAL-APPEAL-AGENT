"""The overview: jobs, drafts, costs and times. Any role; the account comes from the logged-in
user. An admin sees the whole account and its cost; any other role sees their own jobs and no
cost."""

from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser, DbSession, current_user, job_scope
from src.review.overview import Overview, read_overview

router = APIRouter(tags=["overview"])


@router.get("/overview")
def get_overview(
    user: Annotated[CurrentUser, Depends(current_user)], session: DbSession
) -> Overview:
    """Return the job and draft counts the user may see, what the jobs cost (admins only) and
    how long they took."""
    return read_overview(session, job_scope(user))
