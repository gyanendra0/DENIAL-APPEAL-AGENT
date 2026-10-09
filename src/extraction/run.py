"""Extracts a list of stored documents one at a time and saves each result as it arrives.

A document that already has a result for the prompt version, made from the text it has now,
is skipped: a run that stopped half way is continued, not paid for twice.
"""

import logging
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import DocumentType, LlmProvider
from src.db.session import session_scope
from src.extraction.extractor import ExtractionError, ExtractionTruncatedError, extract_document
from src.extraction.prompts import ExtractionPrompt
from src.extraction.store import (
    StoredDocument,
    build_extraction_row,
    read_extraction_rows,
    save_extraction_row,
    text_sha256,
)
from src.llm.gateway import LlmBudgetExceededError, LlmGateway, LlmUnavailableError

logger = logging.getLogger(__name__)

# A progress line is logged each time this many documents have been looked at.
PROGRESS_EVERY = 100
BUDGET_REACHED = "the monthly LLM budget is reached"
NO_PROVIDER = "no provider could answer"


class ExtractionRunSummary(BaseModel):
    """What one run did with the documents it was given.

    `stop_reason` is None when every document was looked at; otherwise it says why the run
    ended early, and `not_tried` documents are left for the next run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected: int = Field(ge=0)
    already_extracted: int = Field(ge=0)
    extracted: int = Field(ge=0)
    failed_to_parse: int = Field(ge=0)
    stopped_for_length: int = Field(ge=0)
    answered_by_fallback: int = Field(ge=0)
    stop_reason: str | None

    @property
    def not_tried(self) -> int:
        return (
            self.selected
            - self.already_extracted
            - self.extracted
            - self.failed_to_parse
            - self.stopped_for_length
        )


def run_extraction(
    session_factory: sessionmaker[Session],
    gateway: LlmGateway,
    prompts: Mapping[DocumentType, ExtractionPrompt],
    documents: Sequence[StoredDocument],
) -> ExtractionRunSummary:
    """Extract each of `documents` that has no up-to-date result, and store the results.

    Spends money and can take hours: one model call per document, several seconds each.
    Each result is committed on its own, so a run that stops keeps what it finished. An
    answer that cannot be used is counted and nothing is stored for it. The run ends early,
    without an error, when the budget is reached or no provider can answer. `prompts` must
    hold one prompt per document type, all of one version.
    """
    versions = {prompt.version for prompt in prompts.values()}
    if len(versions) != 1 or set(prompts) != set(DocumentType):
        raise ValueError("one prompt per document type is needed, all of the same version")
    (prompt_version,) = versions
    with session_scope(session_factory) as session:
        stored = read_extraction_rows(session, prompt_version)

    already_extracted = extracted = failed_to_parse = stopped_for_length = 0
    answered_by_fallback = 0
    stop_reason: str | None = None
    for position, document in enumerate(documents, start=1):
        existing = stored.get(document.key)
        if existing is not None and existing.text_sha256 == text_sha256(document.text):
            already_extracted += 1
        else:
            try:
                result = extract_document(gateway, prompts[document.document_type], document.text)
            except ExtractionTruncatedError:
                stopped_for_length += 1
            except ExtractionError:
                failed_to_parse += 1
            except LlmBudgetExceededError:
                stop_reason = BUDGET_REACHED
                break
            except LlmUnavailableError:
                stop_reason = NO_PROVIDER
                break
            else:
                with session_scope(session_factory) as session:
                    save_extraction_row(session, build_extraction_row(document, result))
                extracted += 1
                answered_by_fallback += result.provider is LlmProvider.FALLBACK
        if position % PROGRESS_EVERY == 0:
            logger.info(
                "extraction progress: %d of %d documents (%d extracted, %d not usable)",
                position,
                len(documents),
                extracted,
                failed_to_parse + stopped_for_length,
            )
    return ExtractionRunSummary(
        selected=len(documents),
        already_extracted=already_extracted,
        extracted=extracted,
        failed_to_parse=failed_to_parse,
        stopped_for_length=stopped_for_length,
        answered_by_fallback=answered_by_fallback,
        stop_reason=stop_reason,
    )
