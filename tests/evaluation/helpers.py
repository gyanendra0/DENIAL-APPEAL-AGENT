"""Made-up stored documents and extraction results for the evaluation tests. No real data."""

from typing import Any

from src.db.models import DocumentType, LlmProvider
from src.extraction.schemas import schema_for
from src.extraction.store import DocumentExtractionRow, DocumentKey, StoredDocument, text_sha256
from src.synth.noise import NoiseLevel
from tests.extraction.helpers import (
    ANSWER_KEYS,
    CLAIM_1,
    LETTER,
    MODEL_NAME,
    PROMPT_VERSION,
    document_text,
    fields_json,
)


def stored_document(
    kind: DocumentType = LETTER,
    claim_id: str = CLAIM_1,
    *,
    noise_level: NoiseLevel = NoiseLevel.NONE,
    missing_field: str | None = None,
) -> StoredDocument:
    return StoredDocument(
        source_claim_id=claim_id,
        document_type=kind,
        text=document_text(claim_id, kind),
        answer_key=ANSWER_KEYS[kind],
        noise_level=noise_level,
        missing_field=missing_field,
    )


def result_row(
    document: StoredDocument,
    *,
    text: str | None = None,
    confidences: dict[str, float] | None = None,
    **changed: Any,
) -> DocumentExtractionRow:
    """A result for `document`: its answer key with `changed` and `confidences` swapped in."""
    raw = fields_json(document.document_type, **changed)
    for name, confidence in (confidences or {}).items():
        raw[name]["confidence"] = confidence
    fields = schema_for(document.document_type).model_validate(raw)
    return DocumentExtractionRow(
        source_claim_id=document.source_claim_id,
        document_type=document.document_type,
        prompt_version=PROMPT_VERSION,
        model_name=MODEL_NAME,
        provider=LlmProvider.PRIMARY,
        text_sha256=text_sha256(document.text if text is None else text),
        fields=fields.model_dump(mode="json"),
    )


def by_key(*rows: DocumentExtractionRow) -> dict[DocumentKey, DocumentExtractionRow]:
    return {row.key: row for row in rows}
