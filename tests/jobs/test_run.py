import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.agent.trace import AgentTrace, StepStatus
from src.db.models import EndState, JobFailure, JobStatus, LlmProvider
from src.db.session import session_scope
from src.drafting.schemas import AppealLetter
from src.extraction.prompts import ExtractionPrompt
from src.extraction.schemas import DenialLetterExtraction
from src.extraction.store import text_sha256
from src.jobs.run import JobContext, UploadLetter, run_upload_job
from src.jobs.store import mark_interrupted_jobs
from src.llm.gateway import LlmRequest, ProviderRejectedError, ProviderUnavailableError
from src.ml.training import WinModel
from tests.agent.helpers import fixed_model
from tests.drafting.helpers import (
    OPENING_TEXT,
    POLICY_TEXT,
    PROMPT,
    drafting_data,
    drafting_gateway,
    good_answer,
)
from tests.extraction.helpers import (
    LETTER,
    MODEL_NAME,
    NOTE,
    PATIENT_NAME,
    PROMPT_VERSION,
    StubProvider,
    fields_json,
    system_text,
)
from tests.jobs.helpers import (
    UPLOAD_TEXT,
    JobResults,
    Member,
    job_accounts,
    queue_job,
    read_job,
    read_results,
)
from tests.rag.helpers import CHUNK_TEXT, MODEL, StubEmbedder

# The made-up letter is dated 2008-03-20, may be appealed until 2008-09-16 and is about 130.10.
BEFORE_THE_DEADLINE = date(2008, 6, 1)
AFTER_THE_DEADLINE = date(2009, 1, 1)
EXTRACTION_PROMPT = ExtractionPrompt(
    version=PROMPT_VERSION, document_type=LETTER, system=system_text(LETTER)
)


@dataclass(frozen=True)
class Setup:
    factory: sessionmaker[Session]
    member: Member
    other: Member


@pytest.fixture
def setup(engine: Engine) -> Iterator[Setup]:
    with drafting_data(engine) as factory, job_accounts(engine, factory) as (member, other):
        yield Setup(factory=factory, member=member, other=other)


def _letter_answer(**changed: Any) -> Callable[[LlmRequest], str]:
    """Answer the extraction with the made-up letter (with `changed`), and the draft well."""

    def answer(request: LlmRequest) -> str:
        if request.system == EXTRACTION_PROMPT.system:
            return json.dumps(fields_json(LETTER, **changed))
        return good_answer(request)

    return answer


def _not_expected(request: object) -> Exception:
    return AssertionError("no call is expected for this job")


def _run(
    setup: Setup,
    job_id: int,
    primary: StubProvider,
    *,
    embedder: StubEmbedder | None = None,
    account_id: int | None = None,
    today: date = BEFORE_THE_DEADLINE,
    model: WinModel | None = None,
) -> JobStatus | None:
    context = JobContext(
        gateway=drafting_gateway(setup.factory, primary, None, embedder),
        extraction_prompt=EXTRACTION_PROMPT,
        drafting_prompt=PROMPT,
        model=model or fixed_model(),
        amount_floor=Decimal("25"),
        win_threshold=0.30,
        embedding_model=MODEL,
    )
    return run_upload_job(
        setup.factory,
        context,
        account_id=setup.member.account_id if account_id is None else account_id,
        job_id=job_id,
        today=today,
    )


def test_a_letter_that_passes_every_step_ends_done_with_a_stored_draft(setup: Setup) -> None:
    job_id = queue_job(setup.factory, setup.member)
    primary = StubProvider(_letter_answer())
    embedder = StubEmbedder()

    status = _run(setup, job_id, primary, embedder=embedder)

    assert status is JobStatus.DONE
    job = read_job(setup.factory, job_id)
    assert (job.status, job.end_state, job.failure) == (
        JobStatus.DONE,
        EndState.NEEDS_REVIEW,
        None,
    )
    assert job.started_at is not None
    assert job.finished_at is not None
    # One extraction call, one embedding call and one drafting call.
    assert (len(primary.requests), len(embedder.requests)) == (2, 1)
    assert primary.requests[0].user == UPLOAD_TEXT
    results = read_results(setup.factory, job_id)
    assert results.extraction is not None
    assert results.extraction.text_sha256 == text_sha256(UPLOAD_TEXT)
    assert results.extraction.prompt_version == PROMPT_VERSION
    assert DenialLetterExtraction.model_validate(results.extraction.fields) == (
        DenialLetterExtraction.model_validate(fields_json(LETTER))
    )
    assert results.agent_run is not None
    trace = AgentTrace.model_validate(results.agent_run.trace)
    assert trace.end_state is EndState.NEEDS_REVIEW
    assert trace.extraction.text_sha256 == text_sha256(UPLOAD_TEXT)
    assert trace.scoring.score == 0.75
    assert results.draft is not None
    assert (results.draft.account_id, results.draft.model_name, results.draft.provider) == (
        setup.member.account_id,
        MODEL_NAME,
        LlmProvider.PRIMARY,
    )
    assert AppealLetter.model_validate(results.draft.letter).paragraphs[1].citations


def test_a_letter_the_rules_stop_ends_done_with_no_draft_and_one_call(setup: Setup) -> None:
    job_id = queue_job(setup.factory, setup.member)
    primary = StubProvider(_letter_answer())
    embedder = StubEmbedder(errors={1: _not_expected(None)})

    status = _run(setup, job_id, primary, embedder=embedder, today=AFTER_THE_DEADLINE)

    assert status is JobStatus.DONE
    assert read_job(setup.factory, job_id).end_state is EndState.STOPPED_BY_RULES
    assert (len(primary.requests), embedder.requests) == (1, [])
    results = read_results(setup.factory, job_id)
    assert (results.extraction is not None, results.agent_run is not None) == (True, True)
    assert results.draft is None


def test_a_letter_below_the_threshold_ends_done_with_no_draft(setup: Setup) -> None:
    job_id = queue_job(setup.factory, setup.member)

    status = _run(setup, job_id, StubProvider(_letter_answer()), model=fixed_model(0.29))

    assert status is JobStatus.DONE
    assert read_job(setup.factory, job_id).end_state is EndState.BELOW_THRESHOLD
    assert read_results(setup.factory, job_id).draft is None


# A missing allowed amount is stopped earlier, by the rules (`must_review`), with the same end.
@pytest.mark.parametrize("missing", ["denial_reason_category", "total_payment_amount"])
def test_a_letter_missing_a_model_input_waits_for_a_person(setup: Setup, missing: str) -> None:
    job_id = queue_job(setup.factory, setup.member)
    primary = StubProvider(_letter_answer(**{missing: None}))

    status = _run(setup, job_id, primary)

    assert status is JobStatus.DONE
    assert read_job(setup.factory, job_id).end_state is EndState.NEEDS_REVIEW_NO_DRAFT
    results = read_results(setup.factory, job_id)
    assert results.agent_run is not None
    trace = AgentTrace.model_validate(results.agent_run.trace)
    assert (trace.scoring.status, trace.scoring.score) == (StepStatus.STOPPED, None)
    assert (len(primary.requests), results.draft) == (1, None)


def test_a_failed_draft_ends_done_as_draft_failed(setup: Setup) -> None:
    job_id = queue_job(setup.factory, setup.member)

    def answer(request: LlmRequest) -> str:
        if request.system == EXTRACTION_PROMPT.system:
            return json.dumps(fields_json(LETTER))
        return "not JSON"

    status = _run(setup, job_id, StubProvider(answer))

    assert status is JobStatus.DONE
    assert read_job(setup.factory, job_id).end_state is EndState.DRAFT_FAILED
    assert read_results(setup.factory, job_id).draft is None


@pytest.mark.parametrize(
    "stub",
    [
        StubProvider(lambda request: "not JSON"),
        StubProvider(_letter_answer(), stop_reason=lambda request: "length"),
    ],
    ids=["not_parseable", "cut_off"],
)
def test_an_unusable_extraction_fails_the_job(setup: Setup, stub: StubProvider) -> None:
    job_id = queue_job(setup.factory, setup.member)

    status = _run(setup, job_id, stub)

    assert status is JobStatus.FAILED
    job = read_job(setup.factory, job_id)
    assert (job.status, job.failure, job.end_state) == (
        JobStatus.FAILED,
        JobFailure.EXTRACTION_FAILED,
        None,
    )
    assert job.finished_at is not None
    assert read_results(setup.factory, job_id) == JobResults(None, None, None)


def test_a_reached_budget_fails_the_job_before_any_call(setup: Setup) -> None:
    job_id = queue_job(setup.factory, setup.member)
    primary = StubProvider(_letter_answer(), highest_cost="5.00")

    assert _run(setup, job_id, primary) is JobStatus.FAILED

    assert read_job(setup.factory, job_id).failure is JobFailure.BUDGET_EXCEEDED
    assert primary.requests == []


def test_no_provider_able_to_answer_fails_the_job(setup: Setup) -> None:
    job_id = queue_job(setup.factory, setup.member)
    primary = StubProvider(error=lambda request: ProviderUnavailableError("timeout"))

    assert _run(setup, job_id, primary) is JobStatus.FAILED

    assert read_job(setup.factory, job_id).failure is JobFailure.LLM_UNAVAILABLE


def test_evidence_without_a_vector_fails_the_job_and_keeps_the_extraction(
    engine: Engine,
) -> None:
    with (
        drafting_data(engine, embedded=False) as factory,
        job_accounts(engine, factory) as (member, other),
    ):
        setup = Setup(factory=factory, member=member, other=other)
        job_id = queue_job(factory, member)

        assert _run(setup, job_id, StubProvider(_letter_answer())) is JobStatus.FAILED

        assert read_job(factory, job_id).failure is JobFailure.EVIDENCE_NOT_READY
        results = read_results(factory, job_id)
        assert results.extraction is not None
        assert (results.agent_run, results.draft) == (None, None)


def test_any_other_error_fails_the_job_and_logs_only_its_class_name(
    setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = queue_job(setup.factory, setup.member)
    primary = StubProvider(
        error=lambda request: ProviderRejectedError(f"refused {PATIENT_NAME} (status 400)")
    )

    with caplog.at_level(logging.INFO, logger="src.jobs.run"):
        status = _run(setup, job_id, primary)

    assert status is JobStatus.FAILED
    assert read_job(setup.factory, job_id).failure is JobFailure.INTERNAL_ERROR
    assert "ProviderRejectedError" in caplog.text
    assert PATIENT_NAME not in caplog.text


def test_a_job_marked_interrupted_while_running_stays_interrupted(
    setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = queue_job(setup.factory, setup.member)
    letter_answer = _letter_answer()

    def answer(request: LlmRequest) -> str:
        # A second API process starting now marks the running job `interrupted`.
        with session_scope(setup.factory) as session:
            mark_interrupted_jobs(session)
        return letter_answer(request)

    with caplog.at_level(logging.INFO, logger="src.jobs.run"):
        status = _run(setup, job_id, StubProvider(answer))

    assert status is JobStatus.FAILED
    job = read_job(setup.factory, job_id)
    assert (job.status, job.failure) == (JobStatus.FAILED, JobFailure.INTERRUPTED)
    results = read_results(setup.factory, job_id)
    assert (results.agent_run, results.draft) == (None, None)
    assert "JobStateError" in caplog.text
    assert "no longer running" in caplog.text


def test_an_extraction_that_is_not_of_a_letter_fails_the_job(setup: Setup) -> None:
    job_id = queue_job(setup.factory, setup.member)
    note_prompt = ExtractionPrompt(
        version=PROMPT_VERSION, document_type=NOTE, system=system_text(NOTE)
    )
    context = JobContext(
        gateway=drafting_gateway(setup.factory, StubProvider(), None, None),
        extraction_prompt=EXTRACTION_PROMPT,
        drafting_prompt=PROMPT,
        model=fixed_model(),
        amount_floor=Decimal("25"),
        win_threshold=0.30,
        embedding_model=MODEL,
    )
    # Past the context's own check: a guard for a prompt swapped after start-up.
    object.__setattr__(context, "extraction_prompt", note_prompt)

    status = run_upload_job(
        setup.factory,
        context,
        account_id=setup.member.account_id,
        job_id=job_id,
        today=BEFORE_THE_DEADLINE,
    )

    assert status is JobStatus.FAILED
    assert read_job(setup.factory, job_id).failure is JobFailure.INTERNAL_ERROR
    assert read_results(setup.factory, job_id).extraction is None


def test_a_job_that_is_not_queued_is_not_run_again(setup: Setup) -> None:
    job_id = queue_job(setup.factory, setup.member)
    _run(setup, job_id, StubProvider(_letter_answer()))
    finished = read_job(setup.factory, job_id)
    primary = StubProvider(error=_not_expected)

    assert _run(setup, job_id, primary) is None

    assert primary.requests == []
    assert read_job(setup.factory, job_id).updated_at == finished.updated_at


def test_another_accounts_job_is_not_run(setup: Setup) -> None:
    job_id = queue_job(setup.factory, setup.member)
    primary = StubProvider(error=_not_expected)

    assert _run(setup, job_id, primary, account_id=setup.other.account_id) is None

    assert primary.requests == []
    job = read_job(setup.factory, job_id)
    assert (job.status, job.started_at) == (JobStatus.QUEUED, None)


def test_the_job_logs_hold_no_document_text_or_extracted_value(
    setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = queue_job(setup.factory, setup.member)

    with caplog.at_level(logging.DEBUG):
        _run(setup, job_id, StubProvider(_letter_answer()))

    assert f"job {job_id} of account {setup.member.account_id} done (needs_review)" in caplog.text
    for forbidden in (
        UPLOAD_TEXT,
        PATIENT_NAME,
        "MBR-000001",
        "130.10",
        CHUNK_TEXT,
        OPENING_TEXT,
        POLICY_TEXT,
    ):
        assert forbidden not in caplog.text


def test_the_extracted_letter_is_left_out_of_the_loop_inputs_repr() -> None:
    letter = DenialLetterExtraction.model_validate(fields_json(LETTER))
    loop_input = UploadLetter(
        text_sha256=text_sha256(UPLOAD_TEXT),
        letter=letter,
        extraction_prompt_version=PROMPT_VERSION,
        features=None,
    )

    assert PATIENT_NAME not in repr(loop_input)


def test_a_job_context_refuses_an_extraction_prompt_that_is_not_for_a_letter() -> None:
    note_prompt = ExtractionPrompt(version=PROMPT_VERSION, document_type=NOTE, system="Made up.")

    with pytest.raises(ValueError, match="denial letter"):
        JobContext(
            gateway=None,  # type: ignore[arg-type]
            extraction_prompt=note_prompt,
            drafting_prompt=PROMPT,
            model=fixed_model(),
            amount_floor=Decimal("25"),
            win_threshold=0.30,
            embedding_model=MODEL,
        )
