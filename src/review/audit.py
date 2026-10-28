"""Writes the audit log: one `audit_events` row for each thing a person did.

`audit_events` is a business table. The caller writes the event in the same transaction as
the change it records, so a change without its event cannot be committed. This module only
inserts; nothing in the code updates or deletes an event. The detail has fixed keys only,
never document text.
"""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.db.models import AuditAction, AuditEvent, DraftRejectReason


class AuditDetail(BaseModel):
    """The only things an event may say besides who, what and which job. Empty by default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int | None = Field(default=None, ge=0)
    reject_reason: DraftRejectReason | None = None


def record_audit_event(
    session: Session,
    account_id: int,
    user_id: int,
    action: AuditAction,
    job_id: int | None,
    detail: AuditDetail | None = None,
) -> None:
    """Add one event of `account_id`. Does not commit: the caller owns the transaction.

    `job_id` is `None` exactly for `account_created`, `user_joined`, `password_changed`,
    `password_reset` and `google_linked`; the table refuses anything else.
    """
    session.add(
        AuditEvent(
            account_id=account_id,
            user_id=user_id,
            action=action,
            job_id=job_id,
            detail=(detail or AuditDetail()).model_dump(mode="json", exclude_none=True),
        )
    )
    session.flush()
