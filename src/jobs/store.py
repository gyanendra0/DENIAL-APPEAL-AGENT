"""Reads and writes the account's upload and job rows.

`uploads`, `jobs`, `upload_extractions`, `upload_agent_runs` and `upload_drafts` are business
tables: every query here filters on `account_id`, and the caller takes the account from the
logged-in user. A job id of another account is answered as not found.

A job moves `queued` -> `running` -> `done` or `failed`, and each move only happens from the
status before it, so a job is never run twice. The times come from the database clock. No
function here commits: the caller owns the transaction.
"""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.agent.trace import AgentTrace
from src.db.models import (
    Account,
    Job,
    JobFailure,
    JobStatus,
    Upload,
    UploadAgentRun,
    UploadDraft,
    UploadExtraction,
    UploadFileType,
)
from src.drafting.drafter import DraftResult
from src.drafting.search_question import SEARCH_QUESTION_VERSION
from src.extraction.extractor import ExtractionResult
from src.extraction.store import text_sha256


class JobNotFoundError(Exception):
    """No job of this id in this account. Its text names the job id only."""


class JobStateError(Exception):
    """The job is not in the status the move needs. Its text names the job id only."""


class NewUpload(BaseModel):
    """One file a user uploaded, as text: what `create_upload_job` stores."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: int
    file_name: str = Field(min_length=1, max_length=255)
    file_type: UploadFileType
    byte_size: int = Field(gt=0)
    # Left out of the repr: document text must never reach a log.
    text: str = Field(min_length=1, repr=False)


def create_upload_job(session: Session, account_id: int, upload: NewUpload) -> int:
    """Store `upload` and its `queued` job in `account_id`, and return the job id.

    The user must belong to `account_id`; the table refuses a user of another account.
    """
    row = Upload(
        account_id=account_id,
        user_id=upload.user_id,
        file_name=upload.file_name,
        file_type=upload.file_type,
        byte_size=upload.byte_size,
        text=upload.text,
        text_sha256=text_sha256(upload.text),
    )
    session.add(row)
    session.flush()
    job = Job(account_id=account_id, upload_id=row.id, status=JobStatus.QUEUED)
    session.add(job)
    session.flush()
    return job.id


def find_job(session: Session, account_id: int, job_id: int) -> Job | None:
    """Return the job `job_id` of `account_id`, or None (also for another account's job)."""
    return session.scalars(
        select(Job).where(Job.account_id == account_id, Job.id == job_id)
    ).one_or_none()


def start_job(session: Session, account_id: int, job_id: int) -> bool:
    """Move a `queued` job of `account_id` to `running`. False when it was not `queued`."""
    started = session.execute(
        update(Job)
        .where(Job.account_id == account_id, Job.id == job_id, Job.status == JobStatus.QUEUED)
        .values(status=JobStatus.RUNNING, started_at=func.now())
        .returning(Job.id)
    ).first()
    return started is not None


def read_upload_text(session: Session, account_id: int, job_id: int) -> str:
    """Return the upload text of a job of `account_id`. Raises `JobNotFoundError`."""
    text = session.scalars(
        select(Upload.text)
        .join(Job, (Job.account_id == Upload.account_id) & (Job.upload_id == Upload.id))
        .where(Job.account_id == account_id, Job.id == job_id)
    ).first()
    if text is None:
        raise JobNotFoundError(f"job {job_id} not found")
    return text


def save_upload_extraction(
    session: Session, account_id: int, job_id: int, text_hash: str, result: ExtractionResult
) -> None:
    """Store the letter fields a model read from the upload of a job of `account_id`.

    `text_hash` is the hash of the text the model read.
    """
    session.add(
        UploadExtraction(
            account_id=account_id,
            job_id=job_id,
            prompt_version=result.prompt_version,
            model_name=result.model_name,
            provider=result.provider,
            text_sha256=text_hash,
            fields=result.fields.model_dump(mode="json"),
        )
    )
    session.flush()


def finish_job_done(
    session: Session,
    account_id: int,
    job_id: int,
    trace: AgentTrace,
    draft: DraftResult | None,
) -> None:
    """Store the trace and the checked letter, if any, and move the `running` job to `done`.

    Raises `JobStateError` when the job is not `running` (it was marked `interrupted`
    meanwhile); nothing is then stored, as the caller rolls back.
    """
    finished = session.execute(
        update(Job)
        .where(Job.account_id == account_id, Job.id == job_id, Job.status == JobStatus.RUNNING)
        .values(status=JobStatus.DONE, end_state=trace.end_state, finished_at=func.now())
        .returning(Job.id)
    ).first()
    if finished is None:
        raise JobStateError(f"job {job_id} is not running")
    session.add(
        UploadAgentRun(
            account_id=account_id,
            job_id=job_id,
            agent_version=trace.agent_version,
            end_state=trace.end_state,
            text_sha256=trace.extraction.text_sha256,
            trace=trace.model_dump(mode="json"),
        )
    )
    if draft is not None:
        session.add(
            UploadDraft(
                account_id=account_id,
                job_id=job_id,
                prompt_version=draft.prompt_version,
                search_question_version=SEARCH_QUESTION_VERSION,
                model_name=draft.model_name,
                provider=draft.provider,
                text_sha256=trace.extraction.text_sha256,
                letter=draft.letter.model_dump(mode="json"),
            )
        )
    session.flush()


def finish_job_failed(session: Session, account_id: int, job_id: int, failure: JobFailure) -> bool:
    """Move a `running` job of `account_id` to `failed` with `failure`.

    False when the job was not `running`; it is then left as it is.
    """
    failed = session.execute(
        update(Job)
        .where(Job.account_id == account_id, Job.id == job_id, Job.status == JobStatus.RUNNING)
        .values(status=JobStatus.FAILED, failure=failure, finished_at=func.now())
        .returning(Job.id)
    ).first()
    return failed is not None


def mark_interrupted_jobs(session: Session) -> int:
    """Fail every `queued` or `running` job as `interrupted`, and return how many there were.

    Run once when the API starts, before it takes a request: a job left unfinished then was
    stopped with the process before it. Goes account by account, so each query on `jobs`
    filters on `account_id`.
    """
    interrupted = 0
    for account_id in session.scalars(select(Account.id).order_by(Account.id)).all():
        interrupted += len(
            session.execute(
                update(Job)
                .where(
                    Job.account_id == account_id,
                    Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
                )
                .values(
                    status=JobStatus.FAILED,
                    failure=JobFailure.INTERRUPTED,
                    finished_at=func.now(),
                )
                .returning(Job.id)
            ).all()
        )
    return interrupted
