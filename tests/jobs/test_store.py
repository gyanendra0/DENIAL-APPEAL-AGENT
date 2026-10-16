from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.agent.trace import AGENT_VERSION, AgentTrace, ExtractionStep, RulesStep, StepStatus
from src.db.models import (
    EndState,
    Job,
    JobFailure,
    JobStatus,
    LlmProvider,
    Upload,
    UploadAgentRun,
    UploadDraft,
    UploadExtraction,
)
from src.drafting.drafter import DraftResult
from src.drafting.schemas import AppealLetter
from src.drafting.search_question import SEARCH_QUESTION_VERSION
from src.extraction.extractor import ExtractionResult
from src.extraction.schemas import DenialLetterExtraction
from src.extraction.store import text_sha256
from src.jobs.store import (
    JobNotFoundError,
    JobStateError,
    create_upload_job,
    find_job,
    finish_job_done,
    finish_job_failed,
    mark_interrupted_jobs,
    read_upload_text,
    save_upload_extraction,
    start_job,
)
from src.rules.verdict import RULES_VERSION
from tests.extraction.helpers import LETTER, MODEL_NAME, PROMPT_VERSION, fields_json
from tests.jobs.helpers import UPLOAD_TEXT, Member, add_member, new_upload

TEXT_HASH = text_sha256(UPLOAD_TEXT)
TRACE = AgentTrace(
    end_state=EndState.STOPPED_BY_RULES,
    extraction=ExtractionStep(
        status=StepStatus.DONE, prompt_version=PROMPT_VERSION, text_sha256=TEXT_HASH
    ),
    rules=RulesStep(
        status=StepStatus.STOPPED,
        rules_version=RULES_VERSION,
        today=date(2026, 10, 16),
        amount_floor=Decimal("25"),
    ),
)
DRAFT = DraftResult(
    letter=AppealLetter.model_validate(
        {"paragraphs": [{"kind": "other", "text": "A made-up opening.", "citations": []}]}
    ),
    provider=LlmProvider.PRIMARY,
    model_name=MODEL_NAME,
    prompt_version=PROMPT_VERSION,
)


@pytest.fixture
def members(session: Session) -> tuple[Member, Member]:
    return add_member(session, "Made Up Store A"), add_member(session, "Made Up Store B")


def _job(session: Session, job_id: int) -> Job:
    return session.scalars(select(Job).where(Job.id == job_id)).one()


def _running_job(session: Session, member: Member) -> int:
    job_id = create_upload_job(session, member.account_id, new_upload(member))
    assert start_job(session, member.account_id, job_id)
    return job_id


def test_an_upload_is_stored_with_its_hash_and_a_queued_job(
    session: Session, members: tuple[Member, Member]
) -> None:
    member, _ = members

    job_id = create_upload_job(session, member.account_id, new_upload(member))

    job = _job(session, job_id)
    assert (job.account_id, job.status, job.started_at, job.finished_at) == (
        member.account_id,
        JobStatus.QUEUED,
        None,
        None,
    )
    upload = session.get(Upload, job.upload_id)
    assert upload is not None
    assert (upload.account_id, upload.user_id, upload.text) == (
        member.account_id,
        member.user_id,
        UPLOAD_TEXT,
    )
    assert (upload.text_sha256, upload.byte_size) == (TEXT_HASH, len(UPLOAD_TEXT))


def test_an_upload_naming_a_user_of_another_account_is_refused(
    session: Session, members: tuple[Member, Member]
) -> None:
    first, second = members

    with pytest.raises(IntegrityError):
        create_upload_job(session, first.account_id, new_upload(second))


def test_the_upload_text_is_left_out_of_its_repr(members: tuple[Member, Member]) -> None:
    assert UPLOAD_TEXT not in repr(new_upload(members[0]))


def test_a_queued_job_starts_once(session: Session, members: tuple[Member, Member]) -> None:
    member, _ = members
    job_id = create_upload_job(session, member.account_id, new_upload(member))

    assert start_job(session, member.account_id, job_id) is True
    assert start_job(session, member.account_id, job_id) is False

    job = _job(session, job_id)
    assert job.status is JobStatus.RUNNING
    assert job.started_at is not None


def test_another_accounts_job_is_not_started_or_read(
    session: Session, members: tuple[Member, Member]
) -> None:
    first, second = members
    job_id = create_upload_job(session, first.account_id, new_upload(first))

    assert start_job(session, second.account_id, job_id) is False
    with pytest.raises(JobNotFoundError, match=f"job {job_id} not found"):
        read_upload_text(session, second.account_id, job_id)
    assert _job(session, job_id).status is JobStatus.QUEUED


def test_the_upload_text_of_a_job_is_read(session: Session, members: tuple[Member, Member]) -> None:
    member, _ = members
    job_id = create_upload_job(session, member.account_id, new_upload(member))

    assert read_upload_text(session, member.account_id, job_id) == UPLOAD_TEXT


def test_the_extraction_is_stored_for_the_job(
    session: Session, members: tuple[Member, Member]
) -> None:
    member, _ = members
    job_id = _running_job(session, member)
    result = ExtractionResult(
        document_type=LETTER,
        fields=DenialLetterExtraction.model_validate(fields_json(LETTER)),
        provider=LlmProvider.PRIMARY,
        model_name=MODEL_NAME,
        prompt_version=PROMPT_VERSION,
    )

    save_upload_extraction(session, member.account_id, job_id, TEXT_HASH, result)

    row = session.scalars(select(UploadExtraction).where(UploadExtraction.job_id == job_id)).one()
    assert (row.account_id, row.prompt_version, row.model_name, row.text_sha256) == (
        member.account_id,
        PROMPT_VERSION,
        MODEL_NAME,
        TEXT_HASH,
    )
    assert DenialLetterExtraction.model_validate(row.fields) == result.fields


def test_a_finished_job_stores_its_trace_and_draft(
    session: Session, members: tuple[Member, Member]
) -> None:
    member, _ = members
    job_id = _running_job(session, member)

    finish_job_done(session, member.account_id, job_id, TRACE, DRAFT)

    job = _job(session, job_id)
    assert (job.status, job.end_state, job.failure) == (
        JobStatus.DONE,
        EndState.STOPPED_BY_RULES,
        None,
    )
    assert job.finished_at is not None
    run = session.scalars(select(UploadAgentRun).where(UploadAgentRun.job_id == job_id)).one()
    assert (run.account_id, run.agent_version, run.end_state, run.text_sha256) == (
        member.account_id,
        AGENT_VERSION,
        EndState.STOPPED_BY_RULES,
        TEXT_HASH,
    )
    assert AgentTrace.model_validate(run.trace) == TRACE
    draft = session.scalars(select(UploadDraft).where(UploadDraft.job_id == job_id)).one()
    assert (draft.prompt_version, draft.search_question_version, draft.provider) == (
        PROMPT_VERSION,
        SEARCH_QUESTION_VERSION,
        LlmProvider.PRIMARY,
    )
    assert AppealLetter.model_validate(draft.letter) == DRAFT.letter


def test_a_finished_job_without_a_letter_stores_no_draft(
    session: Session, members: tuple[Member, Member]
) -> None:
    member, _ = members
    job_id = _running_job(session, member)

    finish_job_done(session, member.account_id, job_id, TRACE, None)

    assert session.scalars(select(UploadDraft).where(UploadDraft.job_id == job_id)).all() == []


def test_a_job_that_is_not_running_cannot_finish(
    session: Session, members: tuple[Member, Member]
) -> None:
    first, second = members
    queued = create_upload_job(session, first.account_id, new_upload(first))
    running = _running_job(session, first)

    with pytest.raises(JobStateError, match=f"job {queued} is not running"):
        finish_job_done(session, first.account_id, queued, TRACE, DRAFT)
    with pytest.raises(JobStateError):
        finish_job_done(session, second.account_id, running, TRACE, DRAFT)

    assert session.scalars(select(UploadAgentRun)).all() == []
    assert _job(session, running).status is JobStatus.RUNNING


def test_a_running_job_fails_with_its_code(
    session: Session, members: tuple[Member, Member]
) -> None:
    member, _ = members
    job_id = _running_job(session, member)

    assert finish_job_failed(session, member.account_id, job_id, JobFailure.BUDGET_EXCEEDED)

    job = _job(session, job_id)
    assert (job.status, job.failure, job.end_state) == (
        JobStatus.FAILED,
        JobFailure.BUDGET_EXCEEDED,
        None,
    )
    assert job.finished_at is not None


def test_only_a_running_job_of_the_account_can_fail(
    session: Session, members: tuple[Member, Member]
) -> None:
    first, second = members
    queued = create_upload_job(session, first.account_id, new_upload(first))
    running = _running_job(session, first)

    assert not finish_job_failed(session, first.account_id, queued, JobFailure.INTERNAL_ERROR)
    assert not finish_job_failed(session, second.account_id, running, JobFailure.INTERNAL_ERROR)

    assert (_job(session, queued).status, _job(session, running).status) == (
        JobStatus.QUEUED,
        JobStatus.RUNNING,
    )


def test_unfinished_jobs_of_every_account_are_marked_interrupted(
    session: Session, members: tuple[Member, Member]
) -> None:
    first, second = members
    queued = create_upload_job(session, first.account_id, new_upload(first))
    running = _running_job(session, second)
    done = _running_job(session, first)
    finish_job_done(session, first.account_id, done, TRACE, None)

    assert mark_interrupted_jobs(session) == 2

    for job_id in (queued, running):
        job = _job(session, job_id)
        assert (job.status, job.failure) == (JobStatus.FAILED, JobFailure.INTERRUPTED)
        assert job.finished_at is not None
    assert _job(session, queued).started_at is None
    assert _job(session, done).status is JobStatus.DONE
    assert mark_interrupted_jobs(session) == 0


def test_finds_a_job_of_its_own_account(session: Session, members: tuple[Member, Member]) -> None:
    member, _ = members
    job_id = create_upload_job(session, member.account_id, new_upload(member))
    found = find_job(session, member.account_id, job_id)
    assert found is not None
    assert (found.id, found.status) == (job_id, JobStatus.QUEUED)


def test_finds_no_job_of_another_account_or_an_unknown_id(
    session: Session, members: tuple[Member, Member]
) -> None:
    member, other = members
    job_id = create_upload_job(session, member.account_id, new_upload(member))
    assert find_job(session, other.account_id, job_id) is None
    assert find_job(session, member.account_id, job_id + 1_000_000) is None
