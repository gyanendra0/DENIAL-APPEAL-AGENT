"""Upload one denial letter, and read the status of the job that turns it into a draft.

The `account_id` comes from the logged-in user, never from the path or the body: a job id of
another account is answered with 404, the same as an id that does not exist. The file's name
and text never reach a log line or an answer.
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict

from src.api.dependencies import (
    CurrentUser,
    DbSession,
    JobRunner,
    current_user,
    get_api_settings,
    get_job_runner,
    require_role,
)
from src.api.roles import UPLOAD_ROLES
from src.api.upload_file import UploadFileError, read_uploaded_letter
from src.api.upload_limit import FILE_TOO_LARGE
from src.config.settings import ApiSettings
from src.db.models import AuditAction, EndState, JobFailure, JobStatus
from src.jobs.store import NewUpload, create_upload_job, find_job
from src.review.audit import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])

MAX_FILE_NAME_CHARACTERS = 255  # the length of `uploads.file_name`
JOB_NOT_FOUND = "job not found"
BAD_FILE_NAME = f"the file needs a name of 1 to {MAX_FILE_NAME_CHARACTERS} characters"


class UploadAccepted(BaseModel):
    """The job an upload queued."""

    model_config = ConfigDict(frozen=True)

    job_id: int
    status: JobStatus


class JobResponse(BaseModel):
    """Where a job is. `failure` is set only when `failed`, `end_state` only when `done`."""

    model_config = ConfigDict(frozen=True)

    job_id: int
    status: JobStatus
    failure: JobFailure | None
    end_state: EndState | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@router.post("/uploads", status_code=status.HTTP_202_ACCEPTED)
def upload_letter(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_role(*UPLOAD_ROLES))],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    runner: Annotated[JobRunner, Depends(get_job_runner)],
    session: DbSession,
) -> UploadAccepted:
    """Store one denial letter (UTF-8 text or a PDF with text), queue its job and audit it.

    The job runs after the answer is sent; read its status with `GET /jobs/{job_id}`.
    """
    data = file.file.read(settings.upload_max_bytes + 1)
    if len(data) > settings.upload_max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, FILE_TOO_LARGE)
    file_name = (file.filename or "").strip()
    if not file_name or len(file_name) > MAX_FILE_NAME_CHARACTERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, BAD_FILE_NAME)
    try:
        file_type, text = read_uploaded_letter(data)
    except UploadFileError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    job_id = create_upload_job(
        session,
        user.account_id,
        NewUpload(
            user_id=user.user_id,
            file_name=file_name,
            file_type=file_type,
            byte_size=len(data),
            text=text,
        ),
    )
    record_audit_event(session, user.account_id, user.user_id, AuditAction.UPLOAD_CREATED, job_id)
    # Added now, run after the answer: by then `DbSession` has committed the queued job.
    background_tasks.add_task(runner, user.account_id, job_id)
    logger.info(
        "upload: account %d, job %d, %s, %d bytes",
        user.account_id,
        job_id,
        file_type.value,
        len(data),
    )
    return UploadAccepted(job_id=job_id, status=JobStatus.QUEUED)


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: DbSession,
) -> JobResponse:
    """Return one job of the user's account, or answer 404."""
    job = find_job(session, user.account_id, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, JOB_NOT_FOUND)
    return JobResponse(
        job_id=job.id,
        status=job.status,
        failure=job.failure,
        end_state=job.end_state,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
