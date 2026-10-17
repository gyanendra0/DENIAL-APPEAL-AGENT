"""The account's audit log, for admins. The account comes from the logged-in user."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import CurrentUser, DbSession, require_role
from src.db.models import UserRole
from src.review.audit_log import AuditPage, list_audit_events
from src.review.paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

router = APIRouter(tags=["audit"])


@router.get("/audit")
def get_audit(
    user: Annotated[CurrentUser, Depends(require_role(UserRole.ADMIN))],
    session: DbSession,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> AuditPage:
    """Return one page of the account's events, newest first. Pass `next_before` as `before`."""
    return list_audit_events(session, user.account_id, before=before, limit=limit)
