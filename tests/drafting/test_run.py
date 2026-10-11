import json
import logging
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    DatasetSplit,
    DenialReasonCategory,
    EvidenceChunk,
    EvidenceDocument,
    EvidenceSource,
    LlmProvider,
)
from src.db.session import session_scope
from src.drafting.citation_validator import CitationProblemKind
from src.drafting.run import (
    BUDGET_REACHED,
    EVIDENCE_NOT_EMBEDDED,
    NO_PROVIDER,
    REQUEST_REFUSED,
    SEARCH_ANSWER_NOT_USABLE,
    DraftingRunSummary,
    run_drafting,
)
from src.drafting.schemas import AppealLetter
from src.drafting.search_question import (
    SEARCH_PHRASES,
    SEARCH_QUESTION_VERSION,
    build_search_question,
)
from src.drafting.store import DraftCandidate, select_draft_candidates
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.extraction.store import text_sha256
from src.llm.gateway import LlmRequest, ProviderRejectedError, ProviderUnavailableError
from tests.drafting.helpers import (
    ABSENT_QUOTE,
    EVIDENCE_DOCUMENT_ID,
    OPENING_TEXT,
    PROMPT,
    absent_quote_answer,
    drafting_data,
    drafting_gateway,
    good_answer,
    is_for,
    stored_drafts,
    two_problem_answer,
    uncited_answer,
    weaken_second_chunk,
)
from tests.extraction.helpers import (
    CLAIM_1,
    CLAIM_2,
    LETTER,
    MODEL_NAME,
    PATIENT_NAME,
    StubProvider,
    document_text,
)
from tests.rag.helpers import CHUNK_TEXT, MODEL, StubEmbedder


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with drafting_data(engine) as session_factory:
        yield session_factory


def _validation(factory: sessionmaker[Session]) -> list[DraftCandidate]:
    with session_scope(factory) as session:
        return select_draft_candidates(session, DatasetSplit.VALIDATION, EXTRACTION_PROMPT_VERSION)


def _run(
    factory: sessionmaker[Session],
    primary: StubProvider,
    *,
    fallback: StubProvider | None = None,
    embedder: StubEmbedder | None = None,
    candidates: list[DraftCandidate] | None = None,
) -> DraftingRunSummary:
    return run_drafting(
        factory,
        drafting_gateway(factory, primary, fallback, embedder),
        PROMPT,
        _validation(factory) if candidates is None else candidates,
        embedding_model=MODEL,
    )


def test_each_claim_is_drafted_and_its_checked_letter_is_stored(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(good_answer)

    summary = _run(factory, primary)

    assert summary == DraftingRunSummary(
        selected=2,
        already_drafted=0,
        drafted=2,
        drafted_with_citation=2,
        blocked=0,
        blocked_by_kind={},
        failed_to_parse=0,
        stopped_for_length=0,
        answered_by_fallback=0,
        stop_reason=None,
    )
    assert summary.not_tried == 0
    drafts = stored_drafts(factory)
    assert set(drafts) == {CLAIM_1, CLAIM_2}
    stored = drafts[CLAIM_1]
    assert stored.model_name == MODEL_NAME
    assert stored.provider is LlmProvider.PRIMARY
    assert stored.search_question_version == SEARCH_QUESTION_VERSION
    assert stored.text_sha256 == text_sha256(document_text(CLAIM_1, LETTER))
    assert [is_for(CLAIM_1)(primary.requests[0]), is_for(CLAIM_2)(primary.requests[1])] == [
        True,
        True,
    ]


def test_a_stored_citation_names_a_chunk_that_exists(factory: sessionmaker[Session]) -> None:
    _run(factory, StubProvider(good_answer))

    letter = AppealLetter.model_validate(stored_drafts(factory)[CLAIM_1].letter)
    assert letter.paragraphs[0].text == OPENING_TEXT
    (citation,) = letter.paragraphs[1].citations
    assert citation.source is EvidenceSource.CMS_NCD
    assert citation.source_document_id == EVIDENCE_DOCUMENT_ID
    assert citation.quote.startswith(CHUNK_TEXT)
    with session_scope(factory) as session:
        chunk_text = session.execute(
            select(EvidenceChunk.text)
            .join(EvidenceDocument, EvidenceChunk.evidence_document_id == EvidenceDocument.id)
            .where(
                EvidenceDocument.source == citation.source,
                EvidenceDocument.source_document_id == citation.source_document_id,
                EvidenceChunk.chunk_index == citation.chunk_index,
                EvidenceChunk.text_sha256 == citation.text_sha256,
            )
        ).scalar_one()
    assert chunk_text == citation.quote


def test_the_search_question_is_the_phrase_of_the_letters_reason_without_its_codes(
    factory: sessionmaker[Session],
) -> None:
    embedder = StubEmbedder()

    _run(factory, StubProvider(good_answer), embedder=embedder)

    question = build_search_question(DenialReasonCategory.NONCOVERED)
    assert [request.texts for request in embedder.requests] == [(question,), (question,)]


def test_a_match_below_the_minimum_similarity_is_not_shown_to_the_model(
    factory: sessionmaker[Session],
) -> None:
    weaken_second_chunk(factory)
    primary = StubProvider(good_answer)

    summary = _run(factory, primary)

    assert summary.drafted_with_citation == 2
    for request in primary.requests:
        (shown,) = json.loads(request.user)["evidence"]
        assert shown["text"].startswith(CHUNK_TEXT)


def test_a_claim_with_no_match_at_the_minimum_is_drafted_with_no_evidence(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(uncited_answer)
    # Question vectors that point away from both stored chunks.
    embedder = StubEmbedder(filler={1: -0.5, 2: -0.5})

    summary = _run(factory, primary, embedder=embedder)

    assert (summary.drafted, summary.drafted_with_citation) == (2, 0)
    assert [json.loads(request.user)["evidence"] for request in primary.requests] == [[], []]


def test_a_letter_without_a_reason_or_lines_is_searched_with_the_phrase_of_other(
    factory: sessionmaker[Session],
) -> None:
    candidate = _validation(factory)[0]
    letter = candidate.letter
    bare = candidate.model_copy(
        update={
            "letter": letter.model_copy(
                update={
                    "denial_reason_category": letter.denial_reason_category.model_copy(
                        update={"value": None}
                    ),
                    "denied_lines": letter.denied_lines.model_copy(update={"value": None}),
                }
            )
        }
    )
    embedder = StubEmbedder()

    summary = _run(factory, StubProvider(good_answer), embedder=embedder, candidates=[bare])

    assert summary.drafted == 1
    assert embedder.requests[0].texts == (SEARCH_PHRASES[DenialReasonCategory.OTHER],)


def test_a_claim_with_a_draft_is_skipped_and_costs_nothing(
    factory: sessionmaker[Session],
) -> None:
    _run(factory, StubProvider(good_answer), candidates=_validation(factory)[:1])
    primary = StubProvider(uncited_answer)
    embedder = StubEmbedder()

    summary = _run(factory, primary, embedder=embedder)

    assert (summary.already_drafted, summary.drafted, summary.not_tried) == (1, 1, 0)
    assert len(primary.requests) == len(embedder.requests) == 1
    assert is_for(CLAIM_2)(primary.requests[0])
    # The first draft is the one that was stored before: it still has its citation.
    first = AppealLetter.model_validate(stored_drafts(factory)[CLAIM_1].letter)
    assert first.paragraphs[1].citations


def test_a_letter_with_no_policy_paragraph_is_stored_and_counted_as_uncited(
    factory: sessionmaker[Session],
) -> None:
    summary = _run(factory, StubProvider(uncited_answer))

    assert (summary.drafted, summary.drafted_with_citation) == (2, 0)
    assert set(stored_drafts(factory)) == {CLAIM_1, CLAIM_2}


def test_a_blocked_draft_is_counted_by_kind_not_stored_and_the_run_goes_on(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(
        lambda request: (
            absent_quote_answer(request) if is_for(CLAIM_1)(request) else good_answer(request)
        )
    )

    summary = _run(factory, primary)

    assert (summary.blocked, summary.drafted, summary.stop_reason) == (1, 1, None)
    assert summary.blocked_by_kind == {CitationProblemKind.QUOTE_NOT_IN_EVIDENCE.value: 1}
    assert summary.not_tried == 0
    assert set(stored_drafts(factory)) == {CLAIM_2}


def test_a_blocked_draft_counts_once_under_each_kind_of_problem_it_has(
    factory: sessionmaker[Session],
) -> None:
    summary = _run(factory, StubProvider(two_problem_answer))

    assert summary.blocked == 2
    assert summary.blocked_by_kind == {
        CitationProblemKind.QUOTE_NOT_IN_EVIDENCE.value: 2,
        CitationProblemKind.POLICY_WITHOUT_CITATION.value: 2,
    }
    assert stored_drafts(factory) == {}


def test_answers_that_cannot_be_used_are_counted_and_nothing_is_stored_for_them(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(
        lambda request: "not JSON" if is_for(CLAIM_1)(request) else good_answer(request),
        stop_reason=lambda request: "length" if is_for(CLAIM_2)(request) else "stop",
    )

    summary = _run(factory, primary)

    assert (summary.failed_to_parse, summary.stopped_for_length) == (1, 1)
    assert (summary.drafted, summary.not_tried, summary.stop_reason) == (0, 0, None)
    assert stored_drafts(factory) == {}


def test_the_run_stops_when_the_budget_is_reached(factory: sessionmaker[Session]) -> None:
    primary = StubProvider(good_answer, highest_cost="5.00")

    summary = _run(factory, primary)

    assert summary.stop_reason == BUDGET_REACHED
    assert (summary.drafted, summary.not_tried) == (0, 2)
    assert primary.requests == []


def test_the_run_stops_when_the_question_does_not_fit_the_budget(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(good_answer)
    embedder = StubEmbedder(highest_cost="5.00")

    summary = _run(factory, primary, embedder=embedder)

    assert summary.stop_reason == BUDGET_REACHED
    assert primary.requests == []
    assert embedder.requests == []


def test_the_run_stops_when_no_provider_can_answer_and_keeps_what_it_finished(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(
        good_answer,
        error=lambda request: (
            None if is_for(CLAIM_1)(request) else ProviderUnavailableError("timeout")
        ),
    )

    summary = _run(factory, primary)

    assert summary.stop_reason == NO_PROVIDER
    assert (summary.drafted, summary.not_tried) == (1, 1)
    assert set(stored_drafts(factory)) == {CLAIM_1}


def test_the_run_stops_when_a_provider_refuses_the_request(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(
        good_answer, error=lambda request: ProviderRejectedError("BadRequestError (status 400)")
    )

    summary = _run(factory, primary)

    assert summary.stop_reason == f"{REQUEST_REFUSED}: BadRequestError (status 400)"
    assert len(primary.requests) == 1
    assert summary.not_tried == 2


def test_the_fallbacks_answer_is_stored_and_counted(factory: sessionmaker[Session]) -> None:
    primary = StubProvider(error=lambda request: ProviderUnavailableError("timeout"))
    fallback = StubProvider(good_answer, model_name="example-fallback-model")

    summary = _run(factory, primary, fallback=fallback)

    assert (summary.drafted, summary.answered_by_fallback) == (2, 2)
    stored = stored_drafts(factory)[CLAIM_1]
    assert (stored.provider, stored.model_name) == (LlmProvider.FALLBACK, "example-fallback-model")


def test_the_run_stops_when_the_question_vector_comes_from_another_model(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(good_answer)
    embedder = StubEmbedder("example-other-embedding-model")

    summary = run_drafting(
        factory,
        drafting_gateway(factory, primary, embedder=embedder),
        PROMPT,
        _validation(factory),
        embedding_model=MODEL,
    )

    assert summary.stop_reason is not None
    assert summary.stop_reason.startswith(f"{SEARCH_ANSWER_NOT_USABLE}: ")
    # One paid question, then the run ends: the second claim would fail the same way.
    assert len(embedder.requests) == 1
    assert primary.requests == []
    assert summary.not_tried == 2


def test_the_run_stops_when_the_number_of_vectors_is_wrong(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(good_answer)

    summary = _run(factory, primary, embedder=StubEmbedder(short_by={1: 1}))

    assert summary.stop_reason is not None
    assert summary.stop_reason.startswith(f"{SEARCH_ANSWER_NOT_USABLE}: ")
    assert primary.requests == []


def test_the_run_stops_before_any_call_when_the_evidence_has_no_vector(engine: Engine) -> None:
    primary = StubProvider(good_answer)
    embedder = StubEmbedder()

    with drafting_data(engine, embedded=False) as factory:
        summary = _run(factory, primary, embedder=embedder)

    assert summary.stop_reason is not None
    assert summary.stop_reason.startswith(f"{EVIDENCE_NOT_EMBEDDED}: ")
    assert "pipelines.embed_evidence_corpus" in summary.stop_reason
    assert primary.requests == []
    assert embedder.requests == []
    assert summary.not_tried == 2


def test_an_empty_list_of_claims_makes_no_call(factory: sessionmaker[Session]) -> None:
    primary = StubProvider(good_answer)

    summary = _run(factory, primary, candidates=[])

    assert (summary.selected, summary.drafted, summary.not_tried) == (0, 0, 0)
    assert primary.requests == []


def test_progress_is_logged_with_counts_and_no_letter_or_evidence_text(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("src.drafting.run.PROGRESS_EVERY", 1)
    caplog.set_level(logging.DEBUG)

    def answer(request: LlmRequest) -> str:
        return absent_quote_answer(request) if is_for(CLAIM_1)(request) else good_answer(request)

    _run(factory, StubProvider(answer))

    assert "drafting progress: 2 of 2 claims (1 drafted, 1 blocked, 0 not usable)" in caplog.text
    for hidden in (PATIENT_NAME, CHUNK_TEXT, ABSENT_QUOTE, OPENING_TEXT, "MBR-000001"):
        assert hidden not in caplog.text
