"""Tests for the review readers. The rows are made up and removed afterwards; nothing is real."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, delete, update
from sqlalchemy.orm import Session, sessionmaker

from src.agent.trace import AGENT_VERSION
from src.db.models import (
    EndState,
    EvidenceChunk,
    GeneratedDocument,
    StoredAppealDraft,
)
from src.db.session import session_scope
from src.drafting.prompts import DRAFTING_PROMPT_VERSION
from src.evaluation.review_store import (
    ReviewableClaims,
    ReviewDataError,
    ReviewItem,
    draft_fingerprint,
    read_review_drafts,
    read_review_items,
    select_reviewable_claims,
)
from tests.drafting.helpers import OPENING_TEXT, POLICY_TEXT
from tests.evaluation.review_helpers import (
    CITED_CHUNK_TEXT,
    add_draft,
    add_reviewable,
    add_run,
    citation,
    cited_letter,
    evidence,
    review_data,
    uncited_letter,
)
from tests.extraction.helpers import CLAIM_1, CLAIM_2, CLAIM_3, LETTER, document_text
from tests.rag.helpers import CHUNK_TEXT

OTHER_END_STATES = [state for state in EndState if state is not EndState.NEEDS_REVIEW]


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with review_data(engine) as session_factory:
        yield session_factory


def _reviewable(factory: sessionmaker[Session]) -> ReviewableClaims:
    with session_scope(factory) as session:
        return select_reviewable_claims(session, AGENT_VERSION, DRAFTING_PROMPT_VERSION)


def _items(factory: sessionmaker[Session], *claim_ids: str) -> list[ReviewItem]:
    with session_scope(factory) as session:
        return read_review_items(session, claim_ids, DRAFTING_PROMPT_VERSION)


def _change_letter_document(factory: sessionmaker[Session], claim_id: str) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(GeneratedDocument)
            .where(
                GeneratedDocument.source_claim_id == claim_id,
                GeneratedDocument.document_type == LETTER,
            )
            .values(text="A newer made-up letter.")
        )


def test_a_needs_review_claim_with_a_draft_can_be_reviewed_in_claim_id_order(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_3)
        add_reviewable(session, CLAIM_1)

    assert _reviewable(factory) == ReviewableClaims(claim_ids=(CLAIM_1, CLAIM_3), out_of_date=0)


@pytest.mark.parametrize("end_state", OTHER_END_STATES)
def test_a_draft_on_a_claim_with_another_end_state_is_never_reviewed(
    factory: sessionmaker[Session], end_state: EndState
) -> None:
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_1)
        add_run(session, CLAIM_2, end_state)
        add_draft(session, CLAIM_2)

    assert _reviewable(factory).claim_ids == (CLAIM_1,)


def test_a_draft_on_a_claim_with_no_trace_is_never_reviewed(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_1)
        add_draft(session, CLAIM_2)

    assert _reviewable(factory) == ReviewableClaims(claim_ids=(CLAIM_1,), out_of_date=0)


def test_a_needs_review_claim_without_a_stored_draft_is_not_reviewed(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_run(session, CLAIM_1)

    assert _reviewable(factory).claim_ids == ()


def test_only_the_named_agent_version_and_prompt_version_count(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_run(session, CLAIM_1, agent_version="v0")
        add_draft(session, CLAIM_1)
        add_run(session, CLAIM_2)
        add_draft(session, CLAIM_2, prompt_version="v0")

    assert _reviewable(factory).claim_ids == ()
    with session_scope(factory) as session:
        assert select_reviewable_claims(session, "v0", DRAFTING_PROMPT_VERSION).claim_ids == (
            CLAIM_1,
        )
        assert select_reviewable_claims(session, AGENT_VERSION, "v0").claim_ids == (CLAIM_2,)


def test_a_draft_whose_letter_document_changed_is_left_out_and_counted(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_1)
        add_reviewable(session, CLAIM_2)
    _change_letter_document(factory, CLAIM_2)

    assert _reviewable(factory) == ReviewableClaims(claim_ids=(CLAIM_1,), out_of_date=1)


def test_a_draft_whose_letter_document_is_gone_is_left_out_and_counted(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_1)
        session.execute(
            delete(GeneratedDocument).where(
                GeneratedDocument.source_claim_id == CLAIM_1,
                GeneratedDocument.document_type == LETTER,
            )
        )

    assert _reviewable(factory) == ReviewableClaims(claim_ids=(), out_of_date=1)


def test_an_item_holds_the_draft_the_letter_document_and_the_full_cited_chunk(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        fingerprint = add_reviewable(session, CLAIM_1)

    (item,) = _items(factory, CLAIM_1)

    assert item.source_claim_id == CLAIM_1
    assert item.draft_sha256 == fingerprint
    assert item.letter == cited_letter()
    assert item.document_text == document_text(CLAIM_1, LETTER)
    assert item.evidence == (evidence(),)


def test_a_chunk_cited_twice_is_shown_once_in_the_order_of_first_use(
    factory: sessionmaker[Session],
) -> None:
    second = f"{CHUNK_TEXT} 1-1."
    letter = cited_letter(citation(1, text=second), citation(), citation(1, text=second))
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_1, letter)

    (item,) = _items(factory, CLAIM_1)

    assert item.evidence == (evidence(1, text=second), evidence())


def test_a_draft_with_no_citation_has_no_evidence_and_items_come_in_claim_id_order(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_2, uncited_letter())
        add_reviewable(session, CLAIM_1)

    first, second = _items(factory, CLAIM_2, CLAIM_1)

    assert (first.source_claim_id, second.source_claim_id) == (CLAIM_1, CLAIM_2)
    assert second.evidence == ()


def test_an_item_holds_nothing_but_what_the_reviewer_may_see() -> None:
    # No win score, no rules verdict and no appeal-success proxy can be carried.
    assert set(ReviewItem.model_fields) == {
        "source_claim_id",
        "draft_sha256",
        "letter",
        "document_text",
        "evidence",
    }
    assert ReviewItem.model_config["extra"] == "forbid"


def test_a_cited_chunk_that_is_gone_refuses_the_export(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_1)
        add_reviewable(session, CLAIM_2, uncited_letter())
        session.execute(delete(EvidenceChunk).where(EvidenceChunk.chunk_index == 0))

    with pytest.raises(ReviewDataError, match="1 citations of the sampled drafts") as raised:
        _items(factory, CLAIM_1, CLAIM_2)

    assert CITED_CHUNK_TEXT not in str(raised.value)


def test_a_cited_chunk_whose_text_changed_refuses_the_export(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_1)
        session.execute(
            update(EvidenceChunk)
            .where(EvidenceChunk.chunk_index == 0)
            .values(text="Newer made-up policy text.", text_sha256="b" * 64)
        )

    with pytest.raises(ReviewDataError, match="no longer stored with the text they quote"):
        _items(factory, CLAIM_1)


def test_a_claim_without_a_letter_document_refuses_the_export(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_reviewable(session, CLAIM_1)
        session.execute(
            delete(GeneratedDocument).where(
                GeneratedDocument.source_claim_id == CLAIM_1,
                GeneratedDocument.document_type == LETTER,
            )
        )

    with pytest.raises(ReviewDataError, match="1 of the 1 claims have no denial letter document"):
        _items(factory, CLAIM_1)


def test_the_stored_drafts_come_with_their_fingerprint_and_whether_they_cite(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        cited = add_draft(session, CLAIM_1)
        uncited = add_draft(session, CLAIM_2, uncited_letter())
        add_draft(session, CLAIM_3)

    with session_scope(factory) as session:
        drafts = read_review_drafts(session, [CLAIM_1, CLAIM_2], DRAFTING_PROMPT_VERSION)

    assert set(drafts) == {CLAIM_1, CLAIM_2}
    assert (drafts[CLAIM_1].draft_sha256, drafts[CLAIM_1].has_citation) == (cited, True)
    assert (drafts[CLAIM_2].draft_sha256, drafts[CLAIM_2].has_citation) == (uncited, False)
    for hidden in (OPENING_TEXT, POLICY_TEXT):
        assert hidden not in repr(drafts[CLAIM_1])


def test_a_claim_without_a_stored_draft_is_an_error(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as session:
        add_draft(session, CLAIM_1)

    with (
        session_scope(factory) as session,
        pytest.raises(ReviewDataError, match="1 of the 2 claims have no stored draft"),
    ):
        read_review_drafts(session, [CLAIM_1, CLAIM_2], DRAFTING_PROMPT_VERSION)


def test_a_stored_letter_that_does_not_fit_the_schema_is_an_error_that_quotes_nothing(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_draft(session, CLAIM_1)
        session.flush()
        session.execute(
            update(StoredAppealDraft)
            .where(StoredAppealDraft.source_claim_id == CLAIM_1)
            .values(letter={"paragraphs": [{"kind": "other", "words": OPENING_TEXT}]})
        )

    with (
        session_scope(factory) as session,
        pytest.raises(ReviewDataError, match="does not fit the letter schema") as raised,
    ):
        read_review_drafts(session, [CLAIM_1], DRAFTING_PROMPT_VERSION)

    assert OPENING_TEXT not in str(raised.value)
    assert raised.value.__cause__ is None


def test_the_fingerprint_follows_the_letter_and_not_the_order_of_its_keys() -> None:
    letter = cited_letter().model_dump(mode="json")
    reordered = {"paragraphs": [dict(reversed(list(p.items()))) for p in letter["paragraphs"]]}
    other = uncited_letter().model_dump(mode="json")

    assert draft_fingerprint(letter) == draft_fingerprint(reordered)
    assert draft_fingerprint(letter) != draft_fingerprint(other)
    assert len(draft_fingerprint(letter)) == 64


def test_a_changed_word_in_the_letter_changes_the_fingerprint() -> None:
    first = uncited_letter("A made-up sentence.").model_dump(mode="json")
    second = uncited_letter("A made-up sentence!").model_dump(mode="json")

    assert draft_fingerprint(first) != draft_fingerprint(second)
