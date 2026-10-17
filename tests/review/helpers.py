"""A made-up account with a job, its stored extraction and a pending draft, for the review tests.

Everything is added in the test's own session, which is rolled back afterwards. The draft
cites two made-up chunks that are stored too. No real letter and no real model is used.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.agent.trace import (
    AgentTrace,
    ExtractionStep,
    RulesStep,
    ScoringStep,
    StepStatus,
)
from src.db.models import (
    AuditEvent,
    EndState,
    EvidenceChunk,
    EvidenceDocument,
    EvidenceSource,
    Job,
    JobStatus,
    LlmProvider,
    UploadAgentRun,
    UploadDraft,
    UploadExtraction,
)
from src.drafting.schemas import AppealLetter
from src.jobs.store import create_upload_job
from src.rules.verdict import RuleOutcome
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


def made_up_trace(end_state: EndState = EndState.NEEDS_REVIEW, score: float = 0.62) -> AgentTrace:
    """A trace that passed the rules and was scored `score`; the later steps are left out."""
    return AgentTrace(
        end_state=end_state,
        extraction=ExtractionStep(
            status=StepStatus.DONE, prompt_version="v2", text_sha256="a" * 64
        ),
        rules=RulesStep(
            status=StepStatus.DONE,
            rules_version="v1",
            today=date(2008, 4, 1),
            amount_floor=Decimal("25"),
            outcome=RuleOutcome.PASS,
        ),
        scoring=ScoringStep(
            status=StepStatus.DONE,
            feature_version="v1",
            model_version="v1",
            score=score,
            threshold=0.30,
        ),
    )


def finish_job(
    session: Session,
    account_id: int,
    job_id: int,
    *,
    trace: AgentTrace | None = None,
    seconds: int = 30,
    cost_usd: str = "0.001000",
) -> None:
    """Move a queued job to `done`, `seconds` after its upload, with `trace` stored."""
    trace = trace or made_up_trace()
    session.execute(
        update(Job)
        .where(Job.account_id == account_id, Job.id == job_id)
        .values(
            status=JobStatus.DONE,
            end_state=trace.end_state,
            started_at=Job.created_at,
            finished_at=Job.created_at + timedelta(seconds=seconds),
            cost_usd=Decimal(cost_usd),
        )
    )
    session.add(
        UploadAgentRun(
            account_id=account_id,
            job_id=job_id,
            agent_version=trace.agent_version,
            end_state=trace.end_state,
            text_sha256="a" * 64,
            trace=trace.model_dump(mode="json"),
        )
    )
    session.flush()


def move_job_back(session: Session, account_id: int, job_id: int, days: int) -> None:
    """Move a job's upload time `days` back, as if it had been uploaded then."""
    session.execute(
        update(Job)
        .where(Job.account_id == account_id, Job.id == job_id)
        .values(created_at=Job.created_at - timedelta(days=days))
    )
    session.flush()
