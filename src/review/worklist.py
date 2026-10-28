"""The worklist: the jobs in a `JobScope`, newest first, one line each, without any document text.

Every table read here is a business table: each one is filtered on `account_id` and each
join carries it, so a line never mixes rows of two accounts. A scope with an owner keeps that
user's uploads only. The denial reason and the win
probability are read out of the stored extraction and trace inside the database, so neither
the letter fields nor the trace leave it. No function here commits or calls a model.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Float, and_, select
from sqlalchemy.orm import Session

from src.db.models import (
    DenialReasonCategory,
    DraftReviewStatus,
    EndState,
    Job,
    JobFailure,
    JobStatus,
    Upload,
    UploadAgentRun,
    UploadDraft,
    UploadExtraction,
    User,
)
from src.jobs.store import JobScope, scope_filter
from src.review.paging import DEFAULT_PAGE_SIZE, check_page_size, next_before


class WorklistItem(BaseModel):
    """One job of the worklist. `review_status` is None when the job has no draft."""

    model_config = ConfigDict(frozen=True)

    job_id: int
    file_name: str
    uploaded_at: datetime
    uploaded_by: str
    status: JobStatus
    failure: JobFailure | None
    end_state: EndState | None
    review_status: DraftReviewStatus | None
    denial_reason: DenialReasonCategory | None
    win_probability: float | None


class WorklistPage(BaseModel):
    """One page of the worklist; `next_before` is None on the last page."""

    model_config = ConfigDict(frozen=True)

    items: tuple[WorklistItem, ...]
    next_before: int | None


def list_worklist(
    session: Session,
    scope: JobScope,
    *,
    before: int | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    status: JobStatus | None = None,
    review_status: DraftReviewStatus | None = None,
) -> WorklistPage:
    """Return one page of the jobs in `scope`, newest first.

    `status` keeps the jobs in that status; `review_status` keeps the jobs whose draft has
    it (a job without a draft is then left out). Raises ValueError on a `limit` out of range.
    """
    check_page_size(limit)
    reason = UploadExtraction.fields["denial_reason_category"]["value"].astext
    score = UploadAgentRun.trace["scoring"]["score"].astext.cast(Float)
    query = (
        select(
            Job.id,
            Upload.file_name,
            Upload.created_at,
            User.email,
            Job.status,
            Job.failure,
            Job.end_state,
            UploadDraft.review_status,
            reason,
            score,
        )
        .join(Upload, and_(Upload.account_id == Job.account_id, Upload.id == Job.upload_id))
        .join(User, and_(User.account_id == Upload.account_id, User.id == Upload.user_id))
        .outerjoin(
            UploadExtraction,
            and_(UploadExtraction.account_id == Job.account_id, UploadExtraction.job_id == Job.id),
        )
        .outerjoin(
            UploadAgentRun,
            and_(UploadAgentRun.account_id == Job.account_id, UploadAgentRun.job_id == Job.id),
        )
        .outerjoin(
            UploadDraft,
            and_(UploadDraft.account_id == Job.account_id, UploadDraft.job_id == Job.id),
        )
        .where(*scope_filter(scope))
        .order_by(Job.id.desc())
        .limit(limit)
    )
    if before is not None:
        query = query.where(Job.id < before)
    if status is not None:
        query = query.where(Job.status == status)
    if review_status is not None:
        query = query.where(UploadDraft.review_status == review_status)
    items = tuple(
        WorklistItem(
            job_id=row[0],
            file_name=row[1],
            uploaded_at=row[2],
            uploaded_by=row[3],
            status=row[4],
            failure=row[5],
            end_state=row[6],
            review_status=row[7],
            denial_reason=None if row[8] is None else DenialReasonCategory(row[8]),
            win_probability=row[9],
        )
        for row in session.execute(query).all()
    )
    return WorklistPage(
        items=items, next_before=next_before([item.job_id for item in items], limit)
    )
