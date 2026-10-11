"""Reads the claims to draft an appeal for and stores the checked letters in `appeal_drafts`.

`claim_sample_labels`, `generated_documents`, `document_extractions` and `appeal_drafts` are
public reference tables (no `account_id`): they hold only the fabricated claims of the
synthetic claims sample and what was made from them.

A claim can be drafted when it is denied and its denial letter has a current extraction: a
`document_extractions` row whose `text_sha256` is the hash of the letter text stored now. A
draft belongs to a claim and a drafting prompt version; a claim that has one is not drafted
again, also when its letter document has changed since.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from src.db.models import (
    ClaimSampleLabel,
    DatasetSplit,
    DocumentExtraction,
    DocumentType,
    GeneratedDocument,
    LlmProvider,
    StoredAppealDraft,
)
from src.drafting.drafter import DraftResult
from src.drafting.search_question import SEARCH_QUESTION_VERSION
from src.extraction.schemas import DenialLetterExtraction
from src.ingest.marketplace_denials import upsert_rows
from src.ml.labels import ClaimId

UPSERT_CONSTRAINT = "uq_appeal_drafts_claim_prompt"
UPSERT_KEY_COLUMNS = ("source_claim_id", "prompt_version")


class LetterExtractionError(Exception):
    """A stored letter extraction no longer fits the schema. Its text quotes no stored value."""


class DraftCandidate(BaseModel):
    """One denied claim that can be drafted: its letter's extracted fields and the letter's hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Left out of the repr: the extracted values must never reach a log.
    letter: DenialLetterExtraction = Field(repr=False)


class AppealDraftRow(BaseModel):
    """One checked letter, shaped like the `appeal_drafts` table.

    `letter` is the validated `AppealLetter` as plain JSON values, because that is what the
    JSON column stores.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    prompt_version: str = Field(min_length=1, max_length=40)
    search_question_version: str = Field(min_length=1, max_length=40)
    model_name: str = Field(min_length=1, max_length=100)
    provider: LlmProvider
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    letter: dict[str, Any] = Field(min_length=1, repr=False)


def _is_denied_in(split: DatasetSplit) -> ColumnElement[bool]:
    return (ClaimSampleLabel.split == split) & ClaimSampleLabel.is_denied.is_(True)


def _of_current_letter_extractions(
    statement: Select[Any], split: DatasetSplit, extraction_prompt_version: str
) -> Select[Any]:
    """Keep the letter extractions of the denied claims of `split` made from today's text."""
    # The hash of the letter text stored now, in the form the extraction row holds.
    letter_hash = func.encode(func.sha256(func.convert_to(GeneratedDocument.text, "UTF8")), "hex")
    return (
        statement.select_from(DocumentExtraction)
        .join(
            ClaimSampleLabel,
            ClaimSampleLabel.source_claim_id == DocumentExtraction.source_claim_id,
        )
        .join(
            GeneratedDocument,
            GeneratedDocument.source_claim_id == DocumentExtraction.source_claim_id,
        )
        .where(
            _is_denied_in(split),
            GeneratedDocument.document_type == DocumentType.DENIAL_LETTER,
            DocumentExtraction.document_type == DocumentType.DENIAL_LETTER,
            DocumentExtraction.prompt_version == extraction_prompt_version,
            DocumentExtraction.text_sha256 == letter_hash,
        )
    )


def select_draft_candidates(
    session: Session,
    split: DatasetSplit,
    extraction_prompt_version: str,
    limit: int | None = None,
) -> list[DraftCandidate]:
    """Return the denied claims of `split` with a current letter extraction, at most `limit`.

    The extraction is the one of `extraction_prompt_version`. The order is claim id, so the
    same `limit` always picks the same claims. Raises `LetterExtractionError` when a stored
    extraction does not fit `DenialLetterExtraction`.
    """
    rows = session.execute(
        _of_current_letter_extractions(
            select(
                DocumentExtraction.source_claim_id,
                DocumentExtraction.text_sha256,
                DocumentExtraction.fields,
            ),
            split,
            extraction_prompt_version,
        )
        .order_by(DocumentExtraction.source_claim_id)
        .limit(limit)
    )
    candidates = []
    for source_claim_id, text_sha256, fields in rows:
        try:
            letter = DenialLetterExtraction.model_validate(fields)
        except ValidationError:
            # `from None`: the parser's error quotes the stored values.
            raise LetterExtractionError(
                f"a stored letter extraction of prompt version {extraction_prompt_version}"
                " does not fit the letter schema"
            ) from None
        candidates.append(
            DraftCandidate(source_claim_id=source_claim_id, text_sha256=text_sha256, letter=letter)
        )
    return candidates


def count_claims_left_out(
    session: Session, split: DatasetSplit, extraction_prompt_version: str
) -> int:
    """Return how many denied claims of `split` have no current letter extraction."""
    denied = session.execute(
        select(func.count()).select_from(ClaimSampleLabel).where(_is_denied_in(split))
    ).scalar_one()
    with_extraction: int = session.execute(
        _of_current_letter_extractions(select(func.count()), split, extraction_prompt_version)
    ).scalar_one()
    return denied - with_extraction


def read_drafted_claim_ids(session: Session, prompt_version: str) -> set[str]:
    """Return the claims that have a stored draft of the drafting prompt `prompt_version`."""
    return set(
        session.scalars(
            select(StoredAppealDraft.source_claim_id).where(
                StoredAppealDraft.prompt_version == prompt_version
            )
        )
    )


def build_appeal_draft_row(candidate: DraftCandidate, result: DraftResult) -> AppealDraftRow:
    """Shape `result`, the checked draft for `candidate`, for the table."""
    return AppealDraftRow(
        source_claim_id=candidate.source_claim_id,
        prompt_version=result.prompt_version,
        search_question_version=SEARCH_QUESTION_VERSION,
        model_name=result.model_name,
        provider=result.provider,
        text_sha256=candidate.text_sha256,
        letter=result.letter.model_dump(mode="json"),
    )


def save_appeal_draft(session: Session, row: AppealDraftRow) -> None:
    """Store `row`, replacing the draft its claim and prompt version had.

    Only store what `draft_appeal` returned: the table does not check the letter. The claim
    must already be in `claim_samples`. Does not commit: the caller owns the transaction.
    """
    upsert_rows(session, StoredAppealDraft, [row], UPSERT_CONSTRAINT, UPSERT_KEY_COLUMNS)
