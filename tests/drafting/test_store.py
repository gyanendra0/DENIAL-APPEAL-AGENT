import traceback
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    ClaimSampleLabel,
    DatasetSplit,
    DocumentExtraction,
    EvidenceSource,
    GeneratedDocument,
    LlmProvider,
    StoredAppealDraft,
)
from src.db.session import session_scope
from src.drafting.drafter import DraftResult
from src.drafting.schemas import AppealLetter, Citation, LetterParagraph, ParagraphKind
from src.drafting.search_question import SEARCH_QUESTION_VERSION
from src.drafting.store import (
    DraftCandidate,
    LetterExtractionError,
    build_appeal_draft_row,
    count_claims_left_out,
    read_drafted_claim_ids,
    save_appeal_draft,
    select_draft_candidates,
)
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.extraction.store import text_sha256
from tests.drafting.helpers import add_letter_extraction, drafting_data, stored_drafts
from tests.extraction.helpers import (
    CLAIM_1,
    CLAIM_2,
    CLAIM_3,
    LETTER,
    PATIENT_NAME,
    PROMPT_VERSION,
    document_text,
)

VALIDATION = DatasetSplit.VALIDATION
QUOTE = "Made-up policy text that is quoted."


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with drafting_data(engine) as session_factory:
        yield session_factory


def _candidates(
    factory: sessionmaker[Session],
    split: DatasetSplit = VALIDATION,
    limit: int | None = None,
    version: str = EXTRACTION_PROMPT_VERSION,
) -> list[DraftCandidate]:
    with session_scope(factory) as session:
        return select_draft_candidates(session, split, version, limit)


def _left_out(factory: sessionmaker[Session], split: DatasetSplit = VALIDATION) -> int:
    with session_scope(factory) as session:
        return count_claims_left_out(session, split, EXTRACTION_PROMPT_VERSION)


def _result(text: str = "A made-up paragraph.") -> DraftResult:
    return DraftResult(
        letter=AppealLetter(
            paragraphs=(
                LetterParagraph(
                    kind=ParagraphKind.POLICY,
                    text=text,
                    citations=(
                        Citation(
                            source=EvidenceSource.CMS_NCD,
                            source_document_id="901",
                            chunk_index=3,
                            text_sha256=f"{1:064x}",
                            quote=QUOTE,
                        ),
                    ),
                ),
            )
        ),
        provider=LlmProvider.FALLBACK,
        model_name="example-other-model",
        prompt_version=PROMPT_VERSION,
    )


def test_the_denied_claims_of_a_split_come_back_in_claim_order_with_their_letter(
    factory: sessionmaker[Session],
) -> None:
    candidates = _candidates(factory)

    assert [candidate.source_claim_id for candidate in candidates] == [CLAIM_1, CLAIM_2]
    first = candidates[0]
    assert first.text_sha256 == text_sha256(document_text(CLAIM_1, LETTER))
    assert first.letter.claim_number.value == CLAIM_1
    assert first.letter.patient_name.value == PATIENT_NAME
    assert [c.source_claim_id for c in _candidates(factory, DatasetSplit.TEST)] == [CLAIM_3]
    assert _candidates(factory, DatasetSplit.TRAIN) == []
    assert _left_out(factory) == 0


def test_a_limit_keeps_the_first_claims(factory: sessionmaker[Session]) -> None:
    assert [c.source_claim_id for c in _candidates(factory, limit=1)] == [CLAIM_1]


def test_a_claim_whose_letter_changed_after_the_extraction_is_left_out(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(GeneratedDocument)
            .where(
                GeneratedDocument.source_claim_id == CLAIM_1,
                GeneratedDocument.document_type == LETTER,
            )
            .values(text="A newer made-up letter.")
        )

    assert [c.source_claim_id for c in _candidates(factory)] == [CLAIM_2]
    # The limit counts only claims that can be drafted.
    assert [c.source_claim_id for c in _candidates(factory, limit=1)] == [CLAIM_2]
    assert _left_out(factory) == 1


def test_an_extraction_of_another_prompt_version_does_not_count(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(DocumentExtraction)
            .where(DocumentExtraction.source_claim_id == CLAIM_1)
            .values(prompt_version="v1")
        )

    assert [c.source_claim_id for c in _candidates(factory)] == [CLAIM_2]
    assert [c.source_claim_id for c in _candidates(factory, version="v1")] == [CLAIM_1]
    assert _left_out(factory) == 1


def test_a_claim_that_is_not_denied_is_not_a_candidate_and_not_counted_as_left_out(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(ClaimSampleLabel)
            .where(ClaimSampleLabel.source_claim_id == CLAIM_1)
            .values(is_denied=False, denial_reason_category=None, appeal_success_proxy=None)
        )

    assert [c.source_claim_id for c in _candidates(factory)] == [CLAIM_2]
    assert _left_out(factory) == 0


def test_a_stored_extraction_that_no_longer_fits_the_schema_is_an_error_without_values(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(DocumentExtraction)
            .where(DocumentExtraction.source_claim_id == CLAIM_2)
            .values(fields={"patient_name": {"value": PATIENT_NAME, "confidence": 7}})
        )

    with pytest.raises(LetterExtractionError, match="does not fit the letter schema") as raised:
        _candidates(factory)

    shown = "".join(traceback.format_exception(raised.value))
    assert PATIENT_NAME not in shown
    assert raised.value.__cause__ is None


def test_a_candidates_repr_leaves_the_letter_out(factory: sessionmaker[Session]) -> None:
    assert PATIENT_NAME not in repr(_candidates(factory)[0])


def test_a_row_carries_who_made_the_draft_and_what_it_was_made_from(
    factory: sessionmaker[Session],
) -> None:
    candidate = _candidates(factory)[0]

    row = build_appeal_draft_row(candidate, _result())

    assert row.source_claim_id == CLAIM_1
    assert row.prompt_version == PROMPT_VERSION
    assert row.search_question_version == SEARCH_QUESTION_VERSION
    assert row.model_name == "example-other-model"
    assert row.provider is LlmProvider.FALLBACK
    assert row.text_sha256 == candidate.text_sha256
    assert AppealLetter.model_validate(row.letter) == _result().letter
    assert QUOTE not in repr(row)


def test_a_saved_draft_reads_back_as_the_checked_letter(factory: sessionmaker[Session]) -> None:
    candidate = _candidates(factory)[0]

    with session_scope(factory) as session:
        save_appeal_draft(session, build_appeal_draft_row(candidate, _result()))

    stored = stored_drafts(factory)[CLAIM_1]
    assert stored.model_name == "example-other-model"
    assert stored.provider is LlmProvider.FALLBACK
    assert stored.search_question_version == SEARCH_QUESTION_VERSION
    assert stored.text_sha256 == candidate.text_sha256
    letter = AppealLetter.model_validate(stored.letter)
    assert letter == _result().letter
    with session_scope(factory) as session:
        assert read_drafted_claim_ids(session, PROMPT_VERSION) == {CLAIM_1}
        assert read_drafted_claim_ids(session, "v997") == set()


def test_saving_a_claim_and_prompt_version_again_replaces_the_draft(
    factory: sessionmaker[Session],
) -> None:
    candidate = _candidates(factory)[0]
    with session_scope(factory) as session:
        save_appeal_draft(session, build_appeal_draft_row(candidate, _result()))

    with session_scope(factory) as session:
        save_appeal_draft(session, build_appeal_draft_row(candidate, _result("A newer one.")))

    with session_scope(factory) as session:
        count = session.execute(
            select(func.count())
            .select_from(StoredAppealDraft)
            .where(StoredAppealDraft.source_claim_id == CLAIM_1)
        ).scalar_one()
    assert count == 1
    letter = AppealLetter.model_validate(stored_drafts(factory)[CLAIM_1].letter)
    assert letter.paragraphs[0].text == "A newer one."


def test_a_second_extraction_version_of_a_claim_does_not_double_the_candidate(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_letter_extraction(session, CLAIM_1, prompt_version="v1")

    assert [c.source_claim_id for c in _candidates(factory)] == [CLAIM_1, CLAIM_2]
    assert _left_out(factory) == 0
