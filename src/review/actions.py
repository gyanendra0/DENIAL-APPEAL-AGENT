"""Edit, approve and reject a job's draft, each with its audit event in the same transaction.

`upload_drafts`, `draft_revisions`, `upload_extractions` and `audit_events` are business
tables: every query here filters on `account_id`, which the caller takes from the logged-in
user. A job of another account is answered as not found. Roles are checked by the routes.

A draft is `pending` until it is approved or rejected; both are final. Version 0 is the
model's draft, which never changes; each edit stores the next version. Every action first
locks the draft row, so two reviewers acting at once are served one after the other, and the
second one's version is then stale.

An edited letter is checked before it is stored (only allowed changes, citations still in
their chunk, no placeholder, no wrong fact), and the latest version is checked again on
approve, since a chunk may have been reloaded since. No function here commits, and none
calls a model.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    AuditAction,
    DraftRejectReason,
    DraftReviewStatus,
    DraftRevision,
    UploadDraft,
    UploadExtraction,
)
from src.drafting.cited_chunks import read_cited_chunks
from src.drafting.edit_check import find_edit_problems
from src.drafting.letter_check import LetterProblem, check_letter
from src.drafting.schemas import AppealLetter
from src.extraction.schemas import DenialLetterExtraction
from src.extraction.store import text_sha256
from src.review.audit import AuditDetail, record_audit_event

# The version number of the model's own draft.
MODEL_DRAFT_VERSION = 0


class DraftNotFoundError(Exception):
    """No draft for this job in this account. Its text names the job id only."""


class DraftNotPendingError(Exception):
    """The draft was already approved or rejected. Its text names the job id only."""


class StaleDraftVersionError(Exception):
    """The version acted on is not the latest. Its text names the job id and versions only."""


class LetterCheckError(Exception):
    """The letter failed the checks. Carries the problems: kinds and positions, no text."""

    def __init__(self, job_id: int, problems: tuple[LetterProblem, ...]) -> None:
        super().__init__(f"job {job_id}: the letter has {len(problems)} problem(s)")
        self.problems = problems


class LatestDraft(BaseModel):
    """The latest version of a job's draft and its letter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    letter: AppealLetter


def edit_draft(
    session: Session,
    account_id: int,
    user_id: int,
    job_id: int,
    letter: AppealLetter,
    based_on_version: int,
) -> int:
    """Store `letter` as the next version of the job's draft, and return that version.

    `based_on_version` is the version the person edited; it must be the latest.
    Raises `DraftNotFoundError`, `DraftNotPendingError`, `StaleDraftVersionError` or
    `LetterCheckError`; nothing is then stored.
    """
    draft = _lock_pending_draft(session, account_id, job_id)
    latest = _latest_version(session, account_id, job_id)
    _require_latest(job_id, based_on_version, latest)
    problems = find_edit_problems(AppealLetter.model_validate(draft.letter), letter)
    if not problems:
        problems = _letter_problems(session, account_id, job_id, letter)
    if problems:
        raise LetterCheckError(job_id, problems)
    version = latest + 1
    session.add(
        DraftRevision(
            account_id=account_id,
            job_id=job_id,
            version=version,
            letter=letter.model_dump(mode="json"),
            text_sha256=text_sha256(letter.model_dump_json()),
            edited_by=user_id,
        )
    )
    session.flush()
    record_audit_event(
        session,
        account_id,
        user_id,
        AuditAction.DRAFT_EDITED,
        job_id,
        AuditDetail(version=version),
    )
    return version


def approve_draft(
    session: Session, account_id: int, user_id: int, job_id: int, version: int
) -> None:
    """Approve `version` of the job's draft, after checking that version's letter again.

    `version` must be the latest. Raises `DraftNotFoundError`, `DraftNotPendingError`,
    `StaleDraftVersionError` or `LetterCheckError`; nothing is then changed.
    """
    draft = _lock_pending_draft(session, account_id, job_id)
    _require_latest(job_id, version, _latest_version(session, account_id, job_id))
    letter = _letter_of_version(session, account_id, draft, version)
    problems = _letter_problems(session, account_id, job_id, letter)
    if problems:
        raise LetterCheckError(job_id, problems)
    draft.review_status = DraftReviewStatus.APPROVED
    draft.reviewed_by = user_id
    draft.reviewed_at = func.now()
    draft.approved_version = version
    session.flush()
    record_audit_event(
        session,
        account_id,
        user_id,
        AuditAction.DRAFT_APPROVED,
        job_id,
        AuditDetail(version=version),
    )


def reject_draft(
    session: Session, account_id: int, user_id: int, job_id: int, reason: DraftRejectReason
) -> None:
    """Reject the job's draft for a rubric `reason`.

    Raises `DraftNotFoundError` or `DraftNotPendingError`; nothing is then changed.
    """
    draft = _lock_pending_draft(session, account_id, job_id)
    draft.review_status = DraftReviewStatus.REJECTED
    draft.reviewed_by = user_id
    draft.reviewed_at = func.now()
    draft.reject_reason = reason
    session.flush()
    record_audit_event(
        session,
        account_id,
        user_id,
        AuditAction.DRAFT_REJECTED,
        job_id,
        AuditDetail(reject_reason=reason),
    )


def read_latest_draft(session: Session, account_id: int, job_id: int) -> LatestDraft:
    """Return the latest version of the job's draft. Raises `DraftNotFoundError`."""
    draft = session.scalars(
        select(UploadDraft).where(
            UploadDraft.account_id == account_id, UploadDraft.job_id == job_id
        )
    ).one_or_none()
    if draft is None:
        raise DraftNotFoundError(f"job {job_id}: no draft")
    version = _latest_version(session, account_id, job_id)
    return LatestDraft(
        version=version, letter=_letter_of_version(session, account_id, draft, version)
    )


def _lock_pending_draft(session: Session, account_id: int, job_id: int) -> UploadDraft:
    draft = session.scalars(
        select(UploadDraft)
        .where(UploadDraft.account_id == account_id, UploadDraft.job_id == job_id)
        .with_for_update()
    ).one_or_none()
    if draft is None:
        raise DraftNotFoundError(f"job {job_id}: no draft")
    if draft.review_status is not DraftReviewStatus.PENDING:
        raise DraftNotPendingError(f"job {job_id}: the draft is {draft.review_status.value}")
    return draft


def _latest_version(session: Session, account_id: int, job_id: int) -> int:
    latest = session.scalar(
        select(func.max(DraftRevision.version)).where(
            DraftRevision.account_id == account_id, DraftRevision.job_id == job_id
        )
    )
    return MODEL_DRAFT_VERSION if latest is None else latest


def _require_latest(job_id: int, version: int, latest: int) -> None:
    if version != latest:
        raise StaleDraftVersionError(f"job {job_id}: version {version} is not the latest {latest}")


def _letter_of_version(
    session: Session, account_id: int, draft: UploadDraft, version: int
) -> AppealLetter:
    if version == MODEL_DRAFT_VERSION:
        return AppealLetter.model_validate(draft.letter)
    stored: dict[str, Any] = session.scalars(
        select(DraftRevision.letter).where(
            DraftRevision.account_id == account_id,
            DraftRevision.job_id == draft.job_id,
            DraftRevision.version == version,
        )
    ).one()
    return AppealLetter.model_validate(stored)


def _letter_problems(
    session: Session, account_id: int, job_id: int, letter: AppealLetter
) -> tuple[LetterProblem, ...]:
    fields: dict[str, Any] = session.scalars(
        select(UploadExtraction.fields).where(
            UploadExtraction.account_id == account_id, UploadExtraction.job_id == job_id
        )
    ).one()
    return check_letter(
        letter, read_cited_chunks(session, letter), DenialLetterExtraction.model_validate(fields)
    )
