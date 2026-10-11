"""Made-up stored drafts and traces for the draft review tests. No real data, no model.

`review_data` is `drafting_data`: three made-up claims with a denial letter document each,
and one evidence document with two chunks. The drafts and traces a test adds go with their
claim when the rows are removed.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.agent.trace import AGENT_VERSION
from src.db.models import AgentRun, EndState, EvidenceSource, LlmProvider, StoredAppealDraft
from src.drafting.prompts import DRAFTING_PROMPT_VERSION
from src.drafting.schemas import AppealLetter, Citation, LetterParagraph, ParagraphKind
from src.drafting.search_question import SEARCH_QUESTION_VERSION
from src.evaluation.review_store import ReviewEvidence, ReviewItem, draft_fingerprint
from src.extraction.store import text_sha256
from tests.drafting.helpers import EVIDENCE_DOCUMENT_ID, OPENING_TEXT, POLICY_TEXT, drafting_data
from tests.extraction.helpers import CLAIM_1, LETTER, MODEL_NAME, document_text
from tests.rag.helpers import CHUNK_TEXT, DOCUMENT_TITLE, SECTION_TITLE

# What `add_document` stores for the first chunk of the made-up evidence document.
CITED_CHUNK_TEXT = f"{CHUNK_TEXT} {EVIDENCE_DOCUMENT_ID}-0."
CITED_TITLE = f"{DOCUMENT_TITLE} {EVIDENCE_DOCUMENT_ID}"
CITED_SECTION_NUMBER = f"999.{EVIDENCE_DOCUMENT_ID}"
# Values a trace holds and a reviewer must never see.
HIDDEN_WIN_SCORE = 0.4242
HIDDEN_RULE_OUTCOME = "made_up_rule_outcome"


def citation(chunk_index: int = 0, *, text: str = CITED_CHUNK_TEXT) -> Citation:
    return Citation(
        source=EvidenceSource.CMS_NCD,
        source_document_id=EVIDENCE_DOCUMENT_ID,
        chunk_index=chunk_index,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        quote=text,
    )


def cited_letter(*citations: Citation) -> AppealLetter:
    """A letter whose policy paragraph cites `citations`; by default the first chunk."""
    return AppealLetter(
        paragraphs=(
            LetterParagraph(kind=ParagraphKind.OTHER, text=OPENING_TEXT),
            LetterParagraph(
                kind=ParagraphKind.POLICY, text=POLICY_TEXT, citations=citations or (citation(),)
            ),
        )
    )


def uncited_letter(text: str = OPENING_TEXT) -> AppealLetter:
    return AppealLetter(paragraphs=(LetterParagraph(kind=ParagraphKind.OTHER, text=text),))


def add_draft(
    session: Session,
    claim_id: str,
    letter: AppealLetter | None = None,
    *,
    prompt_version: str = DRAFTING_PROMPT_VERSION,
) -> str:
    """Store a draft built from the letter document of `claim_id`; return its fingerprint."""
    stored = (letter or cited_letter()).model_dump(mode="json")
    session.add(
        StoredAppealDraft(
            source_claim_id=claim_id,
            prompt_version=prompt_version,
            search_question_version=SEARCH_QUESTION_VERSION,
            model_name=MODEL_NAME,
            provider=LlmProvider.PRIMARY,
            text_sha256=text_sha256(document_text(claim_id, LETTER)),
            letter=stored,
        )
    )
    return draft_fingerprint(stored)


def add_run(
    session: Session,
    claim_id: str,
    end_state: EndState = EndState.NEEDS_REVIEW,
    *,
    agent_version: str = AGENT_VERSION,
) -> None:
    """Store a trace of `claim_id`. The table checks only the two values copied into it."""
    session.add(
        AgentRun(
            source_claim_id=claim_id,
            agent_version=agent_version,
            end_state=end_state,
            text_sha256=text_sha256(document_text(claim_id, LETTER)),
            trace={
                "end_state": end_state.value,
                "agent_version": agent_version,
                "rules": {"outcome": HIDDEN_RULE_OUTCOME},
                "scoring": {"win_probability": HIDDEN_WIN_SCORE},
            },
        )
    )


def add_reviewable(session: Session, claim_id: str, letter: AppealLetter | None = None) -> str:
    """Store a `needs_review` trace and a draft for `claim_id`; return the fingerprint."""
    add_run(session, claim_id)
    return add_draft(session, claim_id, letter)


@contextmanager
def review_data(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Commit the made-up claims, their letter documents and the evidence document."""
    with drafting_data(engine, embedded=False) as factory:
        yield factory


def evidence(chunk_index: int = 0, *, text: str = CITED_CHUNK_TEXT) -> ReviewEvidence:
    return ReviewEvidence(
        source=EvidenceSource.CMS_NCD,
        source_document_id=EVIDENCE_DOCUMENT_ID,
        chunk_index=chunk_index,
        title=CITED_TITLE,
        section_number=CITED_SECTION_NUMBER,
        section_title=SECTION_TITLE,
        text=text,
    )


def review_item(
    claim_id: str = CLAIM_1,
    letter: AppealLetter | None = None,
    *found: ReviewEvidence,
    document: str | None = None,
) -> ReviewItem:
    """What a reviewer sees for `claim_id`, built by hand: no database."""
    shown = letter or cited_letter()
    return ReviewItem(
        source_claim_id=claim_id,
        draft_sha256=draft_fingerprint(shown.model_dump(mode="json")),
        letter=shown,
        document_text=document or document_text(claim_id, LETTER),
        evidence=found,
    )
