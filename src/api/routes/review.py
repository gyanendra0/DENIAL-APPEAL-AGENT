"""The worklist, the denial detail, and editing, approving and rejecting a job's draft.

The `account_id` comes from the logged-in user, never from the path or the body: a job id of
another account is answered with 404, the same as an id that does not exist. A non-admin sees
only the jobs of their own uploads (`job_scope`): another user's job is a 404 too. Editing needs a
reviewer or an admin; approving and rejecting need an admin (`src/api/roles.py`). For all three
the 404 is decided before the role, so a role that may not act cannot probe for jobs it may not
see. A refused letter is answered with its problems' kinds and positions, never
its text. No route here calls a model, and nothing is ever sent to an insurer.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.api.dependencies import NOT_ALLOWED, CurrentUser, DbSession, current_user, job_scope
from src.api.roles import DECIDE_ROLES, EDIT_ROLES
from src.api.routes.jobs import JOB_NOT_FOUND
from src.db.models import DraftRejectReason, DraftReviewStatus, JobStatus, UserRole
from src.drafting.letter_check import LetterProblem
from src.drafting.schemas import AppealLetter
from src.jobs.store import find_job
from src.review.actions import (
    DraftNotFoundError,
    DraftNotPendingError,
    LetterCheckError,
    StaleDraftVersionError,
    approve_draft,
    edit_draft,
    reject_draft,
)
from src.review.detail import JobDetail, read_job_detail
from src.review.paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from src.review.worklist import WorklistPage, list_worklist

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review"])

DRAFT_NOT_FOUND = "draft not found"
DRAFT_NOT_PENDING = "the draft was already approved or rejected"
STALE_VERSION = "the draft has a newer version; reload it"
LETTER_REFUSED = "the letter did not pass the checks"


class EditDraftRequest(BaseModel):
    """The whole edited letter and the version it was based on."""

    model_config = ConfigDict(extra="forbid")

    letter: AppealLetter
    based_on_version: int = Field(ge=0)


class ApproveDraftRequest(BaseModel):
    """The version approved; it must be the latest."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)


class RejectDraftRequest(BaseModel):
    """Why the draft is rejected: a rubric reason, never free text."""

    model_config = ConfigDict(extra="forbid")

    reason: DraftRejectReason


class DraftVersionResponse(BaseModel):
    """The version an edit stored."""

    model_config = ConfigDict(frozen=True)

    job_id: int
    version: int


class ReviewResponse(BaseModel):
    """The draft's review status after a decision."""

    model_config = ConfigDict(frozen=True)

    job_id: int
    review_status: DraftReviewStatus


class LetterRefusal(BaseModel):
    """The detail of a 422 for a refused letter: kinds and positions only."""

    model_config = ConfigDict(frozen=True)

    message: str
    problems: tuple[LetterProblem, ...]


User = Annotated[CurrentUser, Depends(current_user)]


@router.get("/worklist")
def get_worklist(
    user: User,
    session: DbSession,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    review_status: DraftReviewStatus | None = None,
) -> WorklistPage:
    """Return one page of the jobs the user may see, newest first. Pass `next_before` as
    `before`."""
    return list_worklist(
        session,
        job_scope(user),
        before=before,
        limit=limit,
        status=job_status,
        review_status=review_status,
    )


@router.get("/jobs/{job_id}/detail")
def get_job_detail(job_id: int, user: User, session: DbSession) -> JobDetail:
    """Return the upload text, the extracted fields, the trace and the draft of one job."""
    detail = read_job_detail(session, job_scope(user), job_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, JOB_NOT_FOUND)
    return detail


@router.put("/jobs/{job_id}/draft")
def put_draft(
    job_id: int, body: EditDraftRequest, user: User, session: DbSession
) -> DraftVersionResponse:
    """Store an edited letter as the next version, after checking it. Reviewer or admin."""
    _require_role_for_job(session, user, job_id, EDIT_ROLES)
    try:
        version = edit_draft(
            session, user.account_id, user.user_id, job_id, body.letter, body.based_on_version
        )
    except (
        DraftNotFoundError,
        DraftNotPendingError,
        StaleDraftVersionError,
        LetterCheckError,
    ) as e:
        raise _review_error(e) from None
    logger.info("draft edited: account %d, job %d, version %d", user.account_id, job_id, version)
    return DraftVersionResponse(job_id=job_id, version=version)


@router.post("/jobs/{job_id}/draft/approve")
def post_approve(
    job_id: int, body: ApproveDraftRequest, user: User, session: DbSession
) -> ReviewResponse:
    """Approve the latest version, after checking it again. Admin only. Nothing is sent."""
    _require_role_for_job(session, user, job_id, DECIDE_ROLES)
    try:
        approve_draft(session, user.account_id, user.user_id, job_id, body.version)
    except (
        DraftNotFoundError,
        DraftNotPendingError,
        StaleDraftVersionError,
        LetterCheckError,
    ) as e:
        raise _review_error(e) from None
    logger.info("draft approved: account %d, job %d", user.account_id, job_id)
    return ReviewResponse(job_id=job_id, review_status=DraftReviewStatus.APPROVED)


@router.post("/jobs/{job_id}/draft/reject")
def post_reject(
    job_id: int, body: RejectDraftRequest, user: User, session: DbSession
) -> ReviewResponse:
    """Reject the draft for a rubric reason. Admin only."""
    _require_role_for_job(session, user, job_id, DECIDE_ROLES)
    try:
        reject_draft(session, user.account_id, user.user_id, job_id, body.reason)
    except (DraftNotFoundError, DraftNotPendingError) as e:
        raise _review_error(e) from None
    logger.info("draft rejected: account %d, job %d", user.account_id, job_id)
    return ReviewResponse(job_id=job_id, review_status=DraftReviewStatus.REJECTED)


def _require_role_for_job(
    session: Session, user: CurrentUser, job_id: int, roles: frozenset[UserRole]
) -> None:
    """Answer 404 for a job the user may not see, then 403 unless the user has one of `roles`."""
    if find_job(session, job_scope(user), job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, JOB_NOT_FOUND)
    if user.role not in roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_ALLOWED)


def _review_error(error: Exception) -> HTTPException:
    """The answer to an error of `src/review/actions.py`. Its message text is never sent."""
    if isinstance(error, DraftNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, DRAFT_NOT_FOUND)
    if isinstance(error, DraftNotPendingError):
        return HTTPException(status.HTTP_409_CONFLICT, DRAFT_NOT_PENDING)
    if isinstance(error, StaleDraftVersionError):
        return HTTPException(status.HTTP_409_CONFLICT, STALE_VERSION)
    if isinstance(error, LetterCheckError):
        refusal = LetterRefusal(message=LETTER_REFUSED, problems=error.problems)
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, refusal.model_dump(mode="json"))
    raise TypeError(f"not a review error: {type(error).__name__}")
