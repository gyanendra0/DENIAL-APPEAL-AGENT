"""The denial detail: everything the split view shows for one job of the account.

The upload text, the extracted letter fields with their confidence, the agent trace (statuses,
versions, the score, the rules' verdict; it never holds text), and the draft: the model's
letter, the latest version and the review. Every table read here is a business table and
every query filters on `account_id`; a job of another account is answered as not found.
No function here commits or calls a model.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from src.agent.trace import AgentTrace
from src.db.models import (
    DraftRejectReason,
    DraftReviewStatus,
    EndState,
    Job,
    JobFailure,
    JobStatus,
    Upload,
    UploadAgentRun,
    UploadDraft,
    UploadExtraction,
    UploadFileType,
    User,
)
from src.drafting.schemas import AppealLetter
from src.extraction.schemas import DenialLetterExtraction
from src.review.actions import read_latest_draft


class DraftDetail(BaseModel):
    """The model's draft (version 0), the latest version and the review of it.

    `latest` is the model's draft itself while nobody has edited it. The reviewer and the
    time are set once the draft is approved or rejected.
    """

    model_config = ConfigDict(frozen=True)

    prompt_version: str
    model_name: str
    original: AppealLetter
    latest_version: int = Field(ge=0)
    latest: AppealLetter
    review_status: DraftReviewStatus
    reviewed_by: str | None
    reviewed_at: datetime | None
    reject_reason: DraftRejectReason | None
    approved_version: int | None


class JobDetail(BaseModel):
    """One job and what it produced. A part the job did not reach is None."""

    model_config = ConfigDict(frozen=True)

    job_id: int
    file_name: str
    file_type: UploadFileType
    uploaded_at: datetime
    uploaded_by: str
    status: JobStatus
    failure: JobFailure | None
    end_state: EndState | None
    started_at: datetime | None
    finished_at: datetime | None
    cost_usd: Decimal
    # Left out of the repr: document text must never reach a log.
    text: str = Field(repr=False)
    extraction: DenialLetterExtraction | None = Field(repr=False)
    trace: AgentTrace | None
    draft: DraftDetail | None


def read_job_detail(session: Session, account_id: int, job_id: int) -> JobDetail | None:
    """Return the detail of the job `job_id` of `account_id`, or None (also for another account)."""
    row = session.execute(
        select(Job, Upload, User.email)
        .join(Upload, and_(Upload.account_id == Job.account_id, Upload.id == Job.upload_id))
        .join(User, and_(User.account_id == Upload.account_id, User.id == Upload.user_id))
        .where(Job.account_id == account_id, Job.id == job_id)
    ).one_or_none()
    if row is None:
        return None
    job, upload, uploaded_by = row
    fields = session.scalars(
        select(UploadExtraction.fields).where(
            UploadExtraction.account_id == account_id, UploadExtraction.job_id == job_id
        )
    ).one_or_none()
    trace = session.scalars(
        select(UploadAgentRun.trace).where(
            UploadAgentRun.account_id == account_id, UploadAgentRun.job_id == job_id
        )
    ).one_or_none()
    return JobDetail(
        job_id=job.id,
        file_name=upload.file_name,
        file_type=upload.file_type,
        uploaded_at=upload.created_at,
        uploaded_by=uploaded_by,
        status=job.status,
        failure=job.failure,
        end_state=job.end_state,
        started_at=job.started_at,
        finished_at=job.finished_at,
        cost_usd=job.cost_usd,
        text=upload.text,
        extraction=None if fields is None else DenialLetterExtraction.model_validate(fields),
        trace=None if trace is None else AgentTrace.model_validate(trace),
        draft=_draft_detail(session, account_id, job_id),
    )


def _draft_detail(session: Session, account_id: int, job_id: int) -> DraftDetail | None:
    row = session.execute(
        select(UploadDraft, User.email)
        .outerjoin(
            User,
            and_(User.account_id == UploadDraft.account_id, User.id == UploadDraft.reviewed_by),
        )
        .where(UploadDraft.account_id == account_id, UploadDraft.job_id == job_id)
    ).one_or_none()
    if row is None:
        return None
    draft, reviewed_by = row
    latest = read_latest_draft(session, account_id, job_id)
    return DraftDetail(
        prompt_version=draft.prompt_version,
        model_name=draft.model_name,
        original=AppealLetter.model_validate(draft.letter),
        latest_version=latest.version,
        latest=latest.letter,
        review_status=draft.review_status,
        reviewed_by=reviewed_by,
        reviewed_at=draft.reviewed_at,
        reject_reason=draft.reject_reason,
        approved_version=draft.approved_version,
    )
