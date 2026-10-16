"""Made-up stored letters, chunks and a denial letter for the stored-letter check tests.

No real policy text, no real letter and no real person is used.
"""

import hashlib
from typing import Any

from src.db.models import EvidenceSource
from src.drafting.letter_check import ChunkRef, StoredChunk
from src.drafting.schemas import AppealLetter, Citation, LetterParagraph, ParagraphKind
from src.extraction.schemas import DenialLetterExtraction

# Made-up policy text. No real determination says this.
CHUNK_TEXT = "Blue widgets are covered when a doctor orders them.\nGreen widgets are never covered."
OTHER_CHUNK_TEXT = "A home visit is covered once in every calendar month."
QUOTE = "Blue widgets are covered when a doctor orders them."
OTHER_QUOTE = "A home visit is covered once in every calendar month."
OPENING_TEXT = "I am writing to appeal the denial of this made-up claim."
POLICY_TEXT = "The made-up determination covers the widget of $130.10 that was ordered."
CLOSING_TEXT = "Thank you for a second look at this made-up claim."
# The one denied line of the made-up letter: allowed 130.10, paid 0.00.
LETTER_VALUES: dict[str, Any] = {
    "claim_number": "123456789012345",
    "service_from_date": "2008-03-01",
    "service_thru_date": "2008-03-02",
    "diagnosis_codes": ["4011"],
    "denied_lines": [
        {
            "line_number": 2,
            "hcpcs_code": "99213",
            "allowed_charge_amount": "130.10",
            "payment_amount": "0.00",
            "reason_category": "noncovered",
        }
    ],
    "total_allowed_charge_amount": "130.10",
    "total_payment_amount": "0.00",
    "denial_reason_category": "noncovered",
    "patient_name": "Zebulon Quillfeather",
    "member_id": "MBR-000001",
    "provider_name": "Made Up Practice",
    "payer_name": "Made Up Health Plan",
    "reference_number": "REF-000001",
    "letter_date": "2008-03-20",
    "appeal_deadline": "2008-09-16",
}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extraction_fields() -> dict[str, Any]:
    """The made-up letter as stored extraction fields (a value and a confidence each)."""
    return {name: {"value": value, "confidence": 0.9} for name, value in LETTER_VALUES.items()}


def extraction() -> DenialLetterExtraction:
    return DenialLetterExtraction.model_validate(extraction_fields())


def citation(
    document_id: str = "901",
    chunk_index: int = 0,
    quote: str = QUOTE,
    chunk_text: str = CHUNK_TEXT,
    source: EvidenceSource = EvidenceSource.CMS_NCD,
) -> Citation:
    return Citation(
        source=source,
        source_document_id=document_id,
        chunk_index=chunk_index,
        text_sha256=sha256(chunk_text),
        quote=quote,
    )


def other(text: str = OPENING_TEXT) -> LetterParagraph:
    return LetterParagraph(kind=ParagraphKind.OTHER, text=text)


def policy(*citations: Citation, text: str = POLICY_TEXT) -> LetterParagraph:
    return LetterParagraph(kind=ParagraphKind.POLICY, text=text, citations=citations)


def letter(*paragraphs: LetterParagraph) -> AppealLetter:
    return AppealLetter(paragraphs=paragraphs)


def good_letter() -> AppealLetter:
    """Opening, one policy paragraph citing two chunks, closing: passes every check."""
    return letter(
        other(),
        policy(
            citation(),
            citation(document_id="902", quote=OTHER_QUOTE, chunk_text=OTHER_CHUNK_TEXT),
        ),
        other(CLOSING_TEXT),
    )


def stored_chunks() -> dict[ChunkRef, StoredChunk]:
    """The two chunks `good_letter` cites, as stored."""
    return {
        ChunkRef(
            source=EvidenceSource.CMS_NCD, source_document_id="901", chunk_index=0
        ): StoredChunk(text_sha256=sha256(CHUNK_TEXT), text=CHUNK_TEXT),
        ChunkRef(
            source=EvidenceSource.CMS_NCD, source_document_id="902", chunk_index=0
        ): StoredChunk(text_sha256=sha256(OTHER_CHUNK_TEXT), text=OTHER_CHUNK_TEXT),
    }
