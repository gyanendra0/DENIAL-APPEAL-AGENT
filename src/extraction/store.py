"""Reads the documents to extract and stores the results in `document_extractions`.

`generated_documents`, `claim_sample_labels` and `document_extractions` are public reference
tables (no `account_id`): they hold only the fabricated documents of the synthetic claims
sample and what a model read from them. A result belongs to a claim, a document type and a
prompt version; `text_sha256` says which text the model read, so a result for a text that
has since been replaced can be told apart.
"""

import hashlib
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    ClaimSampleLabel,
    DatasetSplit,
    DocumentExtraction,
    DocumentType,
    GeneratedDocument,
    LlmProvider,
)
from src.extraction.extractor import ExtractionResult
from src.ingest.marketplace_denials import upsert_rows
from src.ml.labels import ClaimId
from src.synth.noise import NoiseLevel, NoiseRecord

logger = logging.getLogger(__name__)

UPSERT_CONSTRAINT = "uq_document_extractions_claim_type_prompt"
UPSERT_KEY_COLUMNS = ("source_claim_id", "document_type", "prompt_version")

# What names one document: its claim and its type.
DocumentKey = tuple[str, DocumentType]


class StoredDocument(BaseModel):
    """One stored document, with what is needed to extract it and to mark the result.

    `noise_level` is how much scan damage the noise step gave `text`. `missing_field` is the
    answer-key field it blanked in `text`, or None.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    document_type: DocumentType
    # Left out of the repr: document text and its values must never reach a log.
    text: str = Field(min_length=1, repr=False)
    answer_key: dict[str, Any] = Field(min_length=1, repr=False)
    noise_level: NoiseLevel
    missing_field: str | None

    @property
    def key(self) -> DocumentKey:
        return self.source_claim_id, self.document_type


class DocumentExtractionRow(BaseModel):
    """One extraction result, shaped like the `document_extractions` table.

    `fields` is the validated extraction schema as plain JSON values (dates and amounts as
    text), because that is what the JSON column stores.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    document_type: DocumentType
    prompt_version: str = Field(min_length=1, max_length=40)
    model_name: str = Field(min_length=1, max_length=100)
    provider: LlmProvider
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: dict[str, Any] = Field(min_length=1, repr=False)

    @property
    def key(self) -> DocumentKey:
        return self.source_claim_id, self.document_type


def text_sha256(text: str) -> str:
    """Return the SHA-256 of `text` (UTF-8) as 64 lowercase hex characters."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def select_documents(
    session: Session, split: DatasetSplit, limit: int | None = None
) -> list[StoredDocument]:
    """Return the stored documents of the claims in `split`, at most `limit` of them.

    The order is claim id, then document type (letter, note, record), so the same `limit`
    always picks the same documents.
    """
    rows = session.scalars(
        select(GeneratedDocument)
        .join(
            ClaimSampleLabel,
            ClaimSampleLabel.source_claim_id == GeneratedDocument.source_claim_id,
        )
        .where(ClaimSampleLabel.split == split)
        .order_by(GeneratedDocument.source_claim_id, GeneratedDocument.document_type)
        .limit(limit)
    )
    documents = []
    for row in rows:
        noise = NoiseRecord.model_validate(row.noise_record)
        documents.append(
            StoredDocument(
                source_claim_id=row.source_claim_id,
                document_type=row.document_type,
                text=row.text,
                answer_key=row.answer_key,
                noise_level=noise.level,
                missing_field=noise.missing_field,
            )
        )
    return documents


def read_extraction_rows(
    session: Session, prompt_version: str
) -> dict[DocumentKey, DocumentExtractionRow]:
    """Return every stored result of `prompt_version`, by claim and document type."""
    rows = session.scalars(
        select(DocumentExtraction).where(DocumentExtraction.prompt_version == prompt_version)
    )
    return {
        (row.source_claim_id, row.document_type): DocumentExtractionRow(
            source_claim_id=row.source_claim_id,
            document_type=row.document_type,
            prompt_version=row.prompt_version,
            model_name=row.model_name,
            provider=row.provider,
            text_sha256=row.text_sha256,
            fields=row.fields,
        )
        for row in rows
    }


def build_extraction_row(
    document: StoredDocument, result: ExtractionResult
) -> DocumentExtractionRow:
    """Shape `result`, the extraction of `document`, for the table."""
    return DocumentExtractionRow(
        source_claim_id=document.source_claim_id,
        document_type=document.document_type,
        prompt_version=result.prompt_version,
        model_name=result.model_name,
        provider=result.provider,
        text_sha256=text_sha256(document.text),
        fields=result.fields.model_dump(mode="json"),
    )


def save_extraction_row(session: Session, row: DocumentExtractionRow) -> None:
    """Store `row`, replacing the result its claim, document type and prompt version had.

    The claim must already be in `claim_samples`. Does not commit: the caller owns the
    transaction.
    """
    upsert_rows(session, DocumentExtraction, [row], UPSERT_CONSTRAINT, UPSERT_KEY_COLUMNS)
