"""A made-up account with a job, its stored extraction and a pending draft, for the review tests.

Everything is added in the test's own session, which is rolled back afterwards. The draft
cites two made-up chunks that are stored too. No real letter and no real model is used.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    AuditEvent,
    EvidenceChunk,
    EvidenceDocument,
    EvidenceSource,
    LlmProvider,
    UploadDraft,
    UploadExtraction,
)
from src.drafting.schemas import AppealLetter
from src.jobs.store import create_upload_job
from tests.drafting.letter_helpers import (
    CHUNK_TEXT,
    OTHER_CHUNK_TEXT,
    extraction_fields,
    good_letter,
    sha256,
)
from tests.jobs.helpers import Member, add_member, new_upload

ACCOUNT_NAMES = ("Made Up Review Clinic A", "Made Up Review Clinic B")


@dataclass(frozen=True)
class ReviewSetup:
    """A member and their job with a pending draft, and a second account's job and draft."""

    member: Member
    job_id: int
    other_member: Member
    other_job_id: int


def add_chunk(session: Session, document_id: str, text: str) -> None:
    document = EvidenceDocument(
        source=EvidenceSource.CMS_NCD,
        source_document_id=document_id,
        section_number=f"999.{document_id}",
        title=f"Made-up Determination {document_id}",
        version_number=1,
        effective_date=date(2020, 1, 1),
        source_file_date=date(2026, 10, 5),
    )
    session.add(document)
    session.flush()
    session.add(
        EvidenceChunk(
            evidence_document_id=document.id,
            chunk_index=0,
            section_title=None,
            text=text,
            text_sha256=sha256(text),
            chunker_version="v1",
        )
    )
    session.flush()


def add_job_with_draft(session: Session, member: Member, letter: AppealLetter | None = None) -> int:
    job_id = create_upload_job(session, member.account_id, new_upload(member))
    session.add(
        UploadExtraction(
            account_id=member.account_id,
            job_id=job_id,
            prompt_version="v2",
            model_name="example-model",
            provider=LlmProvider.PRIMARY,
            text_sha256="a" * 64,
            fields=extraction_fields(),
        )
    )
    session.add(
        UploadDraft(
            account_id=member.account_id,
            job_id=job_id,
            prompt_version="v3",
            search_question_version="v3",
            model_name="example-model",
            provider=LlmProvider.PRIMARY,
            text_sha256="a" * 64,
            letter=(letter or good_letter()).model_dump(mode="json"),
        )
    )
    session.flush()
    return job_id


def review_setup(session: Session) -> ReviewSetup:
    """The two chunks `good_letter` cites, and one job with a pending draft per account."""
    add_chunk(session, "901", CHUNK_TEXT)
    add_chunk(session, "902", OTHER_CHUNK_TEXT)
    member, other_member = (add_member(session, name) for name in ACCOUNT_NAMES)
    return ReviewSetup(
        member=member,
        job_id=add_job_with_draft(session, member),
        other_member=other_member,
        other_job_id=add_job_with_draft(session, other_member),
    )


def read_draft(session: Session, job_id: int) -> UploadDraft:
    session.expire_all()
    return session.scalars(select(UploadDraft).where(UploadDraft.job_id == job_id)).one()


def audit_events(session: Session, account_id: int) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.account_id == account_id).order_by(AuditEvent.id)
        )
    )
