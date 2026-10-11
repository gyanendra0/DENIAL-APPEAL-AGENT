"""Reads the drafts a reviewer can be shown, and what the reviewer sees beside each of them.

`agent_runs`, `appeal_drafts`, `generated_documents`, `evidence_documents` and
`evidence_chunks` are public reference tables (no `account_id`): they hold only the fabricated
claims of the synthetic claims sample, what was made from them, and public policy text.

A draft can be reviewed when the agent loop ended its claim as `needs_review`, the draft is
stored, and the denial letter document stored now is the one the draft was built from. Such
a draft is what a reviewer would really be shown. Everything here only reads.

Beside a draft the reviewer sees the denial letter document and the cited chunks in full.
The win score, the rules verdict and the appeal-success proxy are never read here, so they
cannot sway a verdict.
"""

import hashlib
import json
from collections.abc import Collection, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from src.db.models import (
    AgentRun,
    DocumentType,
    EndState,
    EvidenceChunk,
    EvidenceDocument,
    EvidenceSource,
    GeneratedDocument,
    StoredAppealDraft,
)
from src.drafting.schemas import AppealLetter
from src.ml.labels import ClaimId

SHA256_PATTERN = r"^[0-9a-f]{64}$"
NOT_EMPTY_PATTERN = r"\S"


class ReviewDataError(Exception):
    """What is stored cannot be shown to a reviewer. Its text gives counts and quotes no text."""


class ReviewableClaims(BaseModel):
    """The claims whose draft can be reviewed, by claim id, and how many were left out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_ids: tuple[ClaimId, ...]
    # `needs_review` claims with a draft whose denial letter document changed after the draft.
    out_of_date: int = Field(ge=0)


class ReviewDraft(BaseModel):
    """One stored draft: its checked letter and the fingerprint of what is stored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    draft_sha256: str = Field(pattern=SHA256_PATTERN)
    # Left out of the repr: letter text is never printed.
    letter: AppealLetter = Field(repr=False)

    @property
    def has_citation(self) -> bool:
        return any(paragraph.citations for paragraph in self.letter.paragraphs)


class ReviewEvidence(BaseModel):
    """One cited chunk in full, with the determination it comes from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EvidenceSource
    source_document_id: str
    chunk_index: int = Field(ge=0)
    title: str
    section_number: str
    section_title: str | None
    # Left out of the repr: policy text can be long.
    text: str = Field(pattern=NOT_EMPTY_PATTERN, repr=False)


class ReviewItem(BaseModel):
    """Everything a reviewer sees for one sampled draft, and nothing else.

    `evidence` holds each cited chunk once, in the order the letter first cites it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    draft_sha256: str = Field(pattern=SHA256_PATTERN)
    letter: AppealLetter = Field(repr=False)
    # Left out of the repr: document text must never reach a log.
    document_text: str = Field(pattern=NOT_EMPTY_PATTERN, repr=False)
    evidence: tuple[ReviewEvidence, ...] = Field(repr=False)


def draft_fingerprint(letter: Mapping[str, Any]) -> str:
    """Return the SHA-256 of a stored `letter` as 64 lowercase hex characters.

    The letter is written as JSON with sorted keys, so the same stored letter always gives
    the same fingerprint, and a claim that is drafted again gives another one.
    """
    canonical = json.dumps(letter, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def select_reviewable_claims(
    session: Session, agent_version: str, prompt_version: str
) -> ReviewableClaims:
    """Return the claims whose draft of `prompt_version` can be reviewed.

    They are the claims with a `needs_review` trace of `agent_version` and a stored draft
    built from the denial letter document stored now. A claim with another end state or with
    no trace is never returned.
    """
    # The hash of the letter text stored now, in the form the draft row holds.
    letter_hash = func.encode(func.sha256(func.convert_to(GeneratedDocument.text, "UTF8")), "hex")
    rows = session.execute(
        select(AgentRun.source_claim_id, StoredAppealDraft.text_sha256 == letter_hash)
        .join(StoredAppealDraft, StoredAppealDraft.source_claim_id == AgentRun.source_claim_id)
        .outerjoin(
            GeneratedDocument,
            and_(
                GeneratedDocument.source_claim_id == AgentRun.source_claim_id,
                GeneratedDocument.document_type == DocumentType.DENIAL_LETTER,
            ),
        )
        .where(
            AgentRun.agent_version == agent_version,
            AgentRun.end_state == EndState.NEEDS_REVIEW,
            StoredAppealDraft.prompt_version == prompt_version,
        )
        .order_by(AgentRun.source_claim_id)
    ).all()
    # A claim with no letter document at all gives no value here, so it is left out too.
    current = tuple(claim_id for claim_id, is_current in rows if is_current)
    return ReviewableClaims(claim_ids=current, out_of_date=len(rows) - len(current))


def read_review_drafts(
    session: Session, claim_ids: Collection[str], prompt_version: str
) -> dict[str, ReviewDraft]:
    """Return the stored draft of `prompt_version` of each of `claim_ids`, by claim id.

    Raises `ReviewDataError` when a claim has no such draft or a stored letter does not fit
    `AppealLetter`.
    """
    rows = session.execute(
        select(StoredAppealDraft.source_claim_id, StoredAppealDraft.letter).where(
            StoredAppealDraft.prompt_version == prompt_version,
            StoredAppealDraft.source_claim_id.in_(claim_ids),
        )
    ).all()
    drafts = {}
    for source_claim_id, stored in rows:
        try:
            letter = AppealLetter.model_validate(stored)
        except ValidationError:
            # `from None`: the parser's error quotes the stored letter.
            raise ReviewDataError(
                f"a stored draft of prompt version {prompt_version} does not fit the letter"
                " schema"
            ) from None
        drafts[source_claim_id] = ReviewDraft(
            source_claim_id=source_claim_id,
            draft_sha256=draft_fingerprint(stored),
            letter=letter,
        )
    missing = len(set(claim_ids) - set(drafts))
    if missing:
        raise ReviewDataError(
            f"{missing} of the {len(set(claim_ids))} claims have no stored draft of prompt"
            f" version {prompt_version}"
        )
    return drafts


def read_review_items(
    session: Session, claim_ids: Collection[str], prompt_version: str
) -> list[ReviewItem]:
    """Return what a reviewer sees for each of `claim_ids`, by claim id.

    Raises `ReviewDataError` when a claim has no draft or no denial letter document, or when
    a cited chunk is no longer stored with the text the draft quoted (the evidence corpus was
    loaded again and changed): a reviewer must see the full chunk behind every citation.
    """
    drafts = read_review_drafts(session, claim_ids, prompt_version)
    texts = {
        source_claim_id: text
        for source_claim_id, text in session.execute(
            select(GeneratedDocument.source_claim_id, GeneratedDocument.text).where(
                GeneratedDocument.document_type == DocumentType.DENIAL_LETTER,
                GeneratedDocument.source_claim_id.in_(claim_ids),
            )
        )
    }
    without_document = len(set(drafts) - set(texts))
    if without_document:
        raise ReviewDataError(
            f"{without_document} of the {len(drafts)} claims have no denial letter document"
        )

    cited = {
        citation.source_document_id
        for draft in drafts.values()
        for paragraph in draft.letter.paragraphs
        for citation in paragraph.citations
    }
    stored = {
        (source, source_document_id, chunk.chunk_index): (chunk, title, section_number)
        for chunk, source, source_document_id, title, section_number in session.execute(
            select(
                EvidenceChunk,
                EvidenceDocument.source,
                EvidenceDocument.source_document_id,
                EvidenceDocument.title,
                EvidenceDocument.section_number,
            )
            .join(EvidenceDocument, EvidenceDocument.id == EvidenceChunk.evidence_document_id)
            .where(EvidenceDocument.source_document_id.in_(cited))
        )
    }

    items = []
    not_as_cited = 0
    for claim_id in sorted(drafts):
        draft = drafts[claim_id]
        evidence: dict[tuple[EvidenceSource, str, int], ReviewEvidence] = {}
        for paragraph in draft.letter.paragraphs:
            for citation in paragraph.citations:
                key = (citation.source, citation.source_document_id, citation.chunk_index)
                found = stored.get(key)
                if found is None or found[0].text_sha256 != citation.text_sha256:
                    not_as_cited += 1
                    continue
                chunk, title, section_number = found
                evidence.setdefault(
                    key,
                    ReviewEvidence(
                        source=citation.source,
                        source_document_id=citation.source_document_id,
                        chunk_index=citation.chunk_index,
                        title=title,
                        section_number=section_number,
                        section_title=chunk.section_title,
                        text=chunk.text,
                    ),
                )
        items.append(
            ReviewItem(
                source_claim_id=claim_id,
                draft_sha256=draft.draft_sha256,
                letter=draft.letter,
                document_text=texts[claim_id],
                evidence=tuple(evidence.values()),
            )
        )
    if not_as_cited:
        raise ReviewDataError(
            f"{not_as_cited} citations of the sampled drafts name a chunk that is no longer"
            " stored with the text they quote: the evidence corpus was loaded again after the"
            " drafts were written"
        )
    return items
