"""Reads the audit log of one account, newest first. Writing it is `src/review/audit.py`.

`audit_events` and `users` are business tables: the query filters on `account_id` and the
join carries it. An event holds no document text, so neither does a page. No function here
commits or calls a model.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from src.db.models import AuditAction, AuditEvent, User
from src.review.audit import AuditDetail
from src.review.paging import DEFAULT_PAGE_SIZE, check_page_size, next_before


class AuditEntry(BaseModel):
    """One event: who did what, on which job, when."""

    model_config = ConfigDict(frozen=True)

    event_id: int
    created_at: datetime
    user_id: int
    user_email: str
    action: AuditAction
    job_id: int | None
    detail: AuditDetail


class AuditPage(BaseModel):
    """One page of the audit log; `next_before` is None on the last page."""

    model_config = ConfigDict(frozen=True)

    items: tuple[AuditEntry, ...]
    next_before: int | None


def list_audit_events(
    session: Session, account_id: int, *, before: int | None = None, limit: int = DEFAULT_PAGE_SIZE
) -> AuditPage:
    """Return one page of the events of `account_id`, newest first.

    Raises ValueError on a `limit` out of range.
    """
    check_page_size(limit)
    query = (
        select(AuditEvent, User.email)
        .join(User, and_(User.account_id == AuditEvent.account_id, User.id == AuditEvent.user_id))
        .where(AuditEvent.account_id == account_id)
        .order_by(AuditEvent.id.desc())
        .limit(limit)
    )
    if before is not None:
        query = query.where(AuditEvent.id < before)
    items = tuple(
        AuditEntry(
            event_id=event.id,
            created_at=event.created_at,
            user_id=event.user_id,
            user_email=email,
            action=event.action,
            job_id=event.job_id,
            detail=AuditDetail.model_validate(event.detail),
        )
        for event, email in session.execute(query).all()
    )
    return AuditPage(items=items, next_before=next_before([item.event_id for item in items], limit))
