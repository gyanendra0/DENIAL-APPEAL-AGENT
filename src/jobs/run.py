"""Runs one upload job: the letter extraction, then the agent loop, then the stored result.

The job runs in the background, after the upload was answered, so nobody is waiting for an
error: every failure is recorded on the job as a fixed code and logged with the job id and
the error's class name only. Document text, extracted values and error messages never reach
the job row or a log line. A job always ends in a stop for a person; nothing is sent.
"""

import logging
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from src.agent.loop import run_agent_for_claim
from src.db.models import DocumentType, EndState, JobFailure, JobStatus
from src.db.session import session_scope
from src.drafting.prompts import DraftingPrompt
from src.extraction.extractor import ExtractionError, extract_document
from src.extraction.prompts import ExtractionPrompt
from src.extraction.schemas import DenialLetterExtraction
from src.extraction.store import text_sha256
from src.jobs.store import (
    finish_job_done,
    finish_job_failed,
    read_upload_text,
    save_upload_extraction,
    start_job,
)
from src.llm.gateway import LlmBudgetExceededError, LlmGateway, LlmUnavailableError
from src.ml.features import ClaimFeatures, build_letter_features
from src.ml.training import WinModel
from src.rag.retrieval import EvidenceNotEmbeddedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobContext:
    """What every job runs with: built once when the API starts.

    `extraction_prompt` must be a denial-letter prompt. `embedding_model` must be the model
    the gateway embeds with.
    """

    gateway: LlmGateway
    extraction_prompt: ExtractionPrompt
    drafting_prompt: DraftingPrompt
    model: WinModel
    amount_floor: Decimal
    win_threshold: float
    embedding_model: str

    def __post_init__(self) -> None:
        if self.extraction_prompt.document_type is not DocumentType.DENIAL_LETTER:
            raise ValueError("a job reads a denial letter: the extraction prompt must be for one")


class UploadLetter(BaseModel):
    """An uploaded letter as the agent loop reads it. It has no claim id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Left out of the repr: the extracted values must never reach a log.
    letter: DenialLetterExtraction = Field(repr=False)
    extraction_prompt_version: str = Field(min_length=1)
    features: ClaimFeatures | None


def run_upload_job(
    session_factory: sessionmaker[Session],
    context: JobContext,
    *,
    account_id: int,
    job_id: int,
    today: date,
) -> JobStatus | None:
    """Run the `queued` job `job_id` of `account_id` and return the status it ended in.

    Spends money and can take several seconds: one extraction call, and, when the rules and
    the score let the letter through, one embedding call and one drafting call. Returns
    None, doing nothing, when the job is not `queued` (already run, or of another account).

    The extraction is stored as soon as it is read. A job ends `done` with the loop's end
    state, its trace and its checked letter, or `failed` with a code: `extraction_failed`,
    `budget_exceeded`, `llm_unavailable`, `evidence_not_ready`, or `internal_error` for any
    other error.
    """
    started_at = time.monotonic()
    with session_scope(session_factory) as session:
        if not start_job(session, account_id, job_id):
            logger.warning("job %d of account %d was not queued; not run", job_id, account_id)
            return None
        text = read_upload_text(session, account_id, job_id)

    failure = JobFailure.INTERNAL_ERROR
    end_state: EndState | None = None
    try:
        end_state = _run_steps(session_factory, context, account_id, job_id, text, today)
    except ExtractionError:
        failure = JobFailure.EXTRACTION_FAILED
    except LlmBudgetExceededError:
        failure = JobFailure.BUDGET_EXCEEDED
    except LlmUnavailableError:
        failure = JobFailure.LLM_UNAVAILABLE
    except EvidenceNotEmbeddedError:
        failure = JobFailure.EVIDENCE_NOT_READY
    except Exception as error:
        # A background job has no caller to raise to: any other error fails the job.
        # The class name only: an error's message can quote the document or the answer.
        logger.error("job %d failed with %s", job_id, type(error).__name__)

    seconds = time.monotonic() - started_at
    if end_state is not None:
        logger.info(
            "job %d of account %d done (%s) in %.1f s", job_id, account_id, end_state.value, seconds
        )
        return JobStatus.DONE
    with session_scope(session_factory) as session:
        recorded = finish_job_failed(session, account_id, job_id, failure)
    if not recorded:
        logger.warning("job %d was no longer running; failure %s not recorded", job_id, failure)
    logger.info(
        "job %d of account %d failed (%s) in %.1f s", job_id, account_id, failure.value, seconds
    )
    return JobStatus.FAILED


def _run_steps(
    session_factory: sessionmaker[Session],
    context: JobContext,
    account_id: int,
    job_id: int,
    text: str,
    today: date,
) -> EndState:
    text_hash = text_sha256(text)
    extraction = extract_document(context.gateway, context.extraction_prompt, text)
    letter = extraction.fields
    if not isinstance(letter, DenialLetterExtraction):
        raise TypeError("the extraction is not of a denial letter")
    with session_scope(session_factory) as session:
        save_upload_extraction(session, account_id, job_id, text_hash, extraction)

    outcome = run_agent_for_claim(
        session_factory,
        context.gateway,
        context.drafting_prompt,
        context.model,
        UploadLetter(
            text_sha256=text_hash,
            letter=letter,
            extraction_prompt_version=extraction.prompt_version,
            features=build_letter_features(letter),
        ),
        today=today,
        amount_floor=context.amount_floor,
        win_threshold=context.win_threshold,
        embedding_model=context.embedding_model,
    )
    with session_scope(session_factory) as session:
        finish_job_done(session, account_id, job_id, outcome.trace, outcome.draft)
    return outcome.trace.end_state
