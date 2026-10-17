"""Reads the stored chunks a letter cites, for `check_letter` in `src/drafting/letter_check.py`.

`evidence_documents` and `evidence_chunks` are public reference tables (no `account_id`),
so the query has no account filter. A chunk is found by its three naming fields; whether its
text is still the cited version is for the check to decide from `text_sha256`.
"""

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from src.db.models import EvidenceChunk, EvidenceDocument
from src.drafting.letter_check import ChunkRef, StoredChunk
from src.drafting.schemas import AppealLetter


def read_cited_chunks(session: Session, letter: AppealLetter) -> dict[ChunkRef, StoredChunk]:
    """Return each stored chunk `letter` cites; a cited chunk that is not stored is left out."""
    refs = {
        ChunkRef.of(citation) for paragraph in letter.paragraphs for citation in paragraph.citations
    }
    if not refs:
        return {}
    rows = session.execute(
        select(
            EvidenceDocument.source,
            EvidenceDocument.source_document_id,
            EvidenceChunk.chunk_index,
            EvidenceChunk.text_sha256,
            EvidenceChunk.text,
        )
        .join(EvidenceDocument, EvidenceDocument.id == EvidenceChunk.evidence_document_id)
        .where(
            tuple_(
                EvidenceDocument.source,
                EvidenceDocument.source_document_id,
                EvidenceChunk.chunk_index,
            ).in_([(ref.source, ref.source_document_id, ref.chunk_index) for ref in refs])
        )
    )
    return {
        ChunkRef(source=source, source_document_id=document_id, chunk_index=index): StoredChunk(
            text_sha256=sha256, text=text
        )
        for source, document_id, index, sha256, text in rows
    }
