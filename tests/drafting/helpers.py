"""Made-up claims, letter extractions and evidence for the drafting run tests, and stub answers.

The rows are really committed (the code under test commits each letter on its own), so
`drafting_data` removes them afterwards. No stored document, no real policy text and no real
model is used.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    DocumentExtraction,
    EvidenceChunk,
    EvidenceChunkEmbedding,
    LlmProvider,
    StoredAppealDraft,
)
from src.db.session import session_scope
from src.drafting.prompts import DraftingPrompt
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.extraction.store import text_sha256
from src.llm.gateway import LlmGateway, LlmRequest
from tests.extraction.helpers import (
    CALL_COST,
    CAP,
    LETTER,
    MODEL_NAME,
    PROMPT_VERSION,
    SPLITS,
    StubProvider,
    document_text,
    fields_json,
    stored_documents,
)
from tests.rag.helpers import CALL_COST as EMBEDDING_COST
from tests.rag.helpers import (
    StubEmbedder,
    add_document,
    evidence_tables,
    padded_vector,
    store_vector,
)

SYSTEM_TEXT = "Draft the made-up appeal letter."
# The drafting prompt carries the version whose `llm_calls` rows `stored_documents` removes.
PROMPT = DraftingPrompt(version=PROMPT_VERSION, system=SYSTEM_TEXT)
OPENING_TEXT = "I am writing to appeal the denial of this made-up claim."
POLICY_TEXT = "The determination covers the widget that was ordered."
ABSENT_QUOTE = "This sentence is in no evidence text at all."
# What one drafted claim costs with the stubs: the question's embedding and the letter.
CLAIM_COST = CALL_COST + EMBEDDING_COST
EVIDENCE_DOCUMENT_ID = "1"


def _answer(*paragraphs: dict[str, Any]) -> str:
    return json.dumps(
        {"paragraphs": [{"kind": "other", "text": OPENING_TEXT, "citations": []}, *paragraphs]}
    )


def good_answer(request: LlmRequest) -> str:
    """A letter whose policy paragraph quotes the whole text of the first match shown."""
    first = json.loads(request.user)["evidence"][0]
    return _answer(
        {
            "kind": "policy",
            "text": POLICY_TEXT,
            "citations": [{"label": first["label"], "quote": first["text"]}],
        }
    )


def uncited_answer(request: LlmRequest) -> str:
    """A letter that makes no policy statement, so it needs no citation."""
    return _answer()


def absent_quote_answer(request: LlmRequest) -> str:
    """A letter whose quote is not in the cited evidence: the validator blocks it."""
    return _answer(
        {
            "kind": "policy",
            "text": POLICY_TEXT,
            "citations": [{"label": "E1", "quote": ABSENT_QUOTE}],
        }
    )


def two_problem_answer(request: LlmRequest) -> str:
    """A blocked letter with two kinds of problem, one of them twice."""
    absent = {"label": "E1", "quote": ABSENT_QUOTE}
    return _answer(
        {"kind": "policy", "text": POLICY_TEXT, "citations": [absent, absent]},
        {"kind": "policy", "text": POLICY_TEXT, "citations": []},
    )


def is_for(claim_id: str) -> Callable[[LlmRequest], bool]:
    """A check that a drafting request carries the letter of `claim_id`."""
    return lambda request: f'"claim_number": "{claim_id}"' in request.user


def drafting_gateway(
    factory: sessionmaker[Session],
    primary: StubProvider,
    fallback: StubProvider | None = None,
    embedder: StubEmbedder | None = None,
) -> LlmGateway:
    return LlmGateway(
        primary=primary,
        fallback=fallback,
        session_factory=factory,
        cap_usd=CAP,
        embedder=embedder or StubEmbedder(),
    )


def add_letter_extraction(
    session: Session,
    claim_id: str,
    *,
    prompt_version: str = EXTRACTION_PROMPT_VERSION,
    **changed: Any,
) -> None:
    """Store a current letter extraction for `claim_id`; its claim number is the claim id."""
    session.add(
        DocumentExtraction(
            source_claim_id=claim_id,
            document_type=LETTER,
            prompt_version=prompt_version,
            model_name=MODEL_NAME,
            provider=LlmProvider.PRIMARY,
            text_sha256=text_sha256(document_text(claim_id, LETTER)),
            fields=fields_json(LETTER, **{"claim_number": claim_id, **changed}),
        )
    )


@contextmanager
def drafting_data(engine: Engine, *, embedded: bool = True) -> Iterator[sessionmaker[Session]]:
    """Commit the made-up claims with a letter extraction each, and one evidence document.

    The evidence document has two chunks; `embedded` says whether they get a vector. All of
    it, the drafts and the tests' calls are removed afterwards (the drafts and the
    extractions go with their claim).
    """
    with stored_documents(engine) as factory, evidence_tables(engine):
        with session_scope(factory) as session:
            for claim_id in SPLITS:
                add_letter_extraction(session, claim_id)
            chunk_ids = add_document(session, EVIDENCE_DOCUMENT_ID, 2)
            if embedded:
                store_vector(session, chunk_ids[0], [0.0], filler=0.5)
                store_vector(session, chunk_ids[1], [-9.0], filler=0.5)
        yield factory


def weaken_second_chunk(factory: sessionmaker[Session]) -> None:
    """Move the second chunk's vector away, so its similarity to any question is under 0.5."""
    with session_scope(factory) as session:
        chunk_id = session.scalars(
            select(EvidenceChunk.id).where(EvidenceChunk.chunk_index == 1)
        ).one()
        session.execute(
            update(EvidenceChunkEmbedding)
            .where(EvidenceChunkEmbedding.evidence_chunk_id == chunk_id)
            .values(embedding=padded_vector([-40.0], 0.5))
        )


def stored_drafts(factory: sessionmaker[Session]) -> dict[str, StoredAppealDraft]:
    """The stored drafts of the tests' prompt version, by claim id."""
    with session_scope(factory) as session:
        rows = session.scalars(
            select(StoredAppealDraft).where(StoredAppealDraft.prompt_version == PROMPT_VERSION)
        ).all()
        session.expunge_all()
        return {row.source_claim_id: row for row in rows}


def spent(claims: int) -> Decimal:
    return CLAIM_COST * claims
