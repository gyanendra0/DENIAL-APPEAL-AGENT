import json
import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

import src.agent
from src.agent.loop import AgentCandidate, run_agent_for_claim
from src.agent.trace import (
    AGENT_VERSION,
    AgentTrace,
    DraftFailure,
    DraftingStep,
    RetrievalStep,
    ScoringStep,
    StepStatus,
)
from src.db.models import (
    DatasetSplit,
    DenialReasonCategory,
    EndState,
    EvidenceSource,
    LlmProvider,
)
from src.db.session import session_scope
from src.drafting.citation_validator import CitationProblemKind
from src.drafting.schemas import AppealLetter
from src.drafting.search_question import (
    MIN_EVIDENCE_SIMILARITY,
    SEARCH_PHRASES,
    SEARCH_QUESTION_VERSION,
    build_search_question,
)
from src.drafting.store import select_draft_candidates
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.extraction.store import text_sha256
from src.llm.gateway import (
    EmbeddingAnswerError,
    LlmBudgetExceededError,
    LlmUnavailableError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from src.ml.features import FEATURE_VERSION, ClaimFeatures
from src.ml.training import MODEL_VERSION, WinModel
from src.rag.retrieval import EvidenceNotEmbeddedError, QueryEmbeddingError
from src.rules.verdict import RULES_VERSION, RuleOutcome, RuleReason
from tests.agent.helpers import fixed_model as _model
from tests.drafting.helpers import (
    ABSENT_QUOTE,
    EVIDENCE_DOCUMENT_ID,
    OPENING_TEXT,
    POLICY_TEXT,
    PROMPT,
    absent_quote_answer,
    drafting_data,
    drafting_gateway,
    good_answer,
    placeholder_answer,
    stored_drafts,
    two_problem_answer,
    uncited_answer,
    weaken_second_chunk,
)
from tests.extraction.helpers import (
    CLAIM_1,
    LETTER,
    MODEL_NAME,
    PATIENT_NAME,
    PROMPT_VERSION,
    StubProvider,
    document_text,
)
from tests.rag.helpers import CHUNK_TEXT, MODEL, StubEmbedder

# The made-up letter is dated 2008-03-20, may be appealed until 2008-09-16 and is about 130.10.
BEFORE_THE_DEADLINE = date(2008, 6, 1)
AFTER_THE_DEADLINE = date(2009, 1, 1)
FLOOR = Decimal("25")
THRESHOLD = 0.30
FEATURES = ClaimFeatures(
    denial_reason_category=DenialReasonCategory.NONCOVERED,
    total_allowed_charge=Decimal("130.10"),
    fully_denied=True,
)
# What `src/agent` must never do: read the clock. "Today" is always passed in.
CLOCK_READ = re.compile(r"\.today\(|\.now\(|\butcnow\b|\bimport time\b|\bfrom time\b")


def _not_expected(request: object) -> Exception:
    return AssertionError("no model call is expected for this claim")


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with drafting_data(engine) as session_factory:
        yield session_factory


def _candidate(factory: sessionmaker[Session], **changed: Any) -> AgentCandidate:
    """The first made-up claim, with the letter values in `changed` replaced."""
    with session_scope(factory) as session:
        draft = select_draft_candidates(
            session, DatasetSplit.VALIDATION, EXTRACTION_PROMPT_VERSION
        )[0]
    letter = draft.letter.model_copy(
        update={
            name: getattr(draft.letter, name).model_copy(update={"value": value})
            for name, value in changed.items()
        }
    )
    return AgentCandidate(
        source_claim_id=draft.source_claim_id,
        text_sha256=draft.text_sha256,
        letter=letter,
        extraction_prompt_version=EXTRACTION_PROMPT_VERSION,
        features=FEATURES,
    )


def _run(
    factory: sessionmaker[Session],
    primary: StubProvider,
    *,
    fallback: StubProvider | None = None,
    embedder: StubEmbedder | None = None,
    candidate: AgentCandidate | None = None,
    model: WinModel | None = None,
    today: date = BEFORE_THE_DEADLINE,
    win_threshold: float = THRESHOLD,
) -> AgentTrace:
    return run_agent_for_claim(
        factory,
        drafting_gateway(factory, primary, fallback, embedder),
        PROMPT,
        model or _model(),
        candidate or _candidate(factory),
        today=today,
        amount_floor=FLOOR,
        win_threshold=win_threshold,
        embedding_model=MODEL,
    )


def test_a_claim_that_passes_every_step_needs_review_and_its_letter_is_stored(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(good_answer)
    embedder = StubEmbedder()

    trace = _run(factory, primary, embedder=embedder)

    assert trace.end_state is EndState.NEEDS_REVIEW
    assert trace.agent_version == AGENT_VERSION
    assert [
        step.status
        for step in (trace.extraction, trace.rules, trace.scoring, trace.retrieval, trace.drafting)
    ] == [StepStatus.DONE] * 5
    # One claim makes one embedding call and one chat call, and no extraction call.
    assert (len(embedder.requests), len(primary.requests)) == (1, 1)
    assert primary.requests[0].system == PROMPT.system
    drafts = stored_drafts(factory)
    assert set(drafts) == {CLAIM_1}
    letter = AppealLetter.model_validate(drafts[CLAIM_1].letter)
    assert letter.paragraphs[1].citations


def test_the_trace_records_the_facts_of_every_step(factory: sessionmaker[Session]) -> None:
    trace = _run(factory, StubProvider(good_answer))

    assert trace.extraction.prompt_version == EXTRACTION_PROMPT_VERSION
    assert trace.extraction.text_sha256 == text_sha256(document_text(CLAIM_1, LETTER))
    assert (trace.rules.rules_version, trace.rules.today, trace.rules.amount_floor) == (
        RULES_VERSION,
        BEFORE_THE_DEADLINE,
        FLOOR,
    )
    assert (trace.rules.outcome, trace.rules.reasons) == (RuleOutcome.PASS, ())
    assert trace.scoring == ScoringStep(
        status=StepStatus.DONE,
        feature_version=FEATURE_VERSION,
        model_version=MODEL_VERSION,
        score=0.75,
        threshold=THRESHOLD,
    )
    assert trace.retrieval.search_question_version == SEARCH_QUESTION_VERSION
    assert trace.retrieval.embedding_model == MODEL
    assert trace.retrieval.min_similarity == MIN_EVIDENCE_SIMILARITY
    closest, second = trace.retrieval.matches
    assert (closest.source, closest.source_document_id, closest.chunk_index) == (
        EvidenceSource.CMS_NCD,
        EVIDENCE_DOCUMENT_ID,
        0,
    )
    assert re.fullmatch(r"[0-9a-f]{64}", closest.text_sha256)
    assert closest.similarity > second.similarity
    assert trace.drafting == DraftingStep(
        status=StepStatus.DONE,
        prompt_version=PROMPT_VERSION,
        provider=LlmProvider.PRIMARY,
        model_name=MODEL_NAME,
        cited_paragraphs=1,
    )


def test_the_search_question_is_the_phrase_of_the_letters_reason_without_its_codes(
    factory: sessionmaker[Session],
) -> None:
    embedder = StubEmbedder()

    _run(factory, StubProvider(good_answer), embedder=embedder)

    question = build_search_question(DenialReasonCategory.NONCOVERED)
    assert [request.texts for request in embedder.requests] == [(question,)]


def test_a_letter_without_a_reason_or_lines_is_searched_with_the_phrase_of_other(
    factory: sessionmaker[Session],
) -> None:
    embedder = StubEmbedder()
    bare = _candidate(factory, denial_reason_category=None, denied_lines=None)

    trace = _run(factory, StubProvider(good_answer), embedder=embedder, candidate=bare)

    assert trace.end_state is EndState.NEEDS_REVIEW
    assert embedder.requests[0].texts == (SEARCH_PHRASES[DenialReasonCategory.OTHER],)


def test_a_match_below_the_minimum_similarity_is_not_shown_to_the_model(
    factory: sessionmaker[Session],
) -> None:
    weaken_second_chunk(factory)
    primary = StubProvider(good_answer)

    trace = _run(factory, primary)

    (shown,) = json.loads(primary.requests[0].user)["evidence"]
    assert shown["text"].startswith(CHUNK_TEXT)
    (kept,) = trace.retrieval.matches
    assert (kept.chunk_index, kept.similarity >= MIN_EVIDENCE_SIMILARITY) == (0, True)


def test_a_claim_with_no_match_at_the_minimum_is_drafted_with_no_evidence(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(uncited_answer)
    # A question vector that points away from both stored chunks.
    embedder = StubEmbedder(filler={1: -0.5})

    trace = _run(factory, primary, embedder=embedder)

    assert json.loads(primary.requests[0].user)["evidence"] == []
    assert trace.end_state is EndState.NEEDS_REVIEW
    assert (trace.retrieval.status, trace.retrieval.matches) == (StepStatus.DONE, ())
    assert trace.retrieval.min_similarity == MIN_EVIDENCE_SIMILARITY
    assert trace.drafting.cited_paragraphs == 0
    letter = AppealLetter.model_validate(stored_drafts(factory)[CLAIM_1].letter)
    assert [paragraph.citations for paragraph in letter.paragraphs] == [()]


def test_a_hard_fail_stops_the_claim_with_no_model_call_and_no_draft(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(error=_not_expected)
    embedder = StubEmbedder(errors={1: _not_expected(None)})

    trace = _run(factory, primary, embedder=embedder, today=AFTER_THE_DEADLINE)

    assert trace.end_state is EndState.STOPPED_BY_RULES
    assert (trace.rules.status, trace.rules.outcome) == (StepStatus.STOPPED, RuleOutcome.HARD_FAIL)
    assert trace.rules.reasons == (RuleReason.DEADLINE_PASSED,)
    assert trace.scoring == ScoringStep(status=StepStatus.NOT_RUN)
    assert trace.retrieval == RetrievalStep(status=StepStatus.NOT_RUN)
    assert trace.drafting == DraftingStep(status=StepStatus.NOT_RUN)
    assert (primary.requests, embedder.requests) == ([], [])
    assert stored_drafts(factory) == {}


def test_a_must_review_verdict_waits_for_a_person_with_no_model_call_and_no_draft(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(error=_not_expected)
    embedder = StubEmbedder(errors={1: _not_expected(None)})
    no_deadline = _candidate(factory, appeal_deadline=None)

    trace = _run(factory, primary, embedder=embedder, candidate=no_deadline)

    assert trace.end_state is EndState.NEEDS_REVIEW_NO_DRAFT
    assert (trace.rules.status, trace.rules.outcome) == (
        StepStatus.STOPPED,
        RuleOutcome.MUST_REVIEW,
    )
    assert trace.rules.reasons == (RuleReason.DEADLINE_MISSING,)
    assert [trace.scoring.status, trace.retrieval.status, trace.drafting.status] == [
        StepStatus.NOT_RUN
    ] * 3
    assert (primary.requests, embedder.requests) == ([], [])
    assert stored_drafts(factory) == {}


def test_an_amount_below_zero_fails_the_rules_step_with_no_model_call(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(error=_not_expected)
    embedder = StubEmbedder(errors={1: _not_expected(None)})
    negative = _candidate(factory, total_allowed_charge_amount=Decimal("-1.00"))

    trace = _run(factory, primary, embedder=embedder, candidate=negative)

    assert trace.end_state is EndState.DRAFT_FAILED
    assert (trace.rules.status, trace.rules.outcome, trace.rules.reasons) == (
        StepStatus.FAILED,
        None,
        (),
    )
    assert trace.scoring.status is StepStatus.NOT_RUN
    assert (primary.requests, embedder.requests) == ([], [])
    assert stored_drafts(factory) == {}


def test_a_score_below_the_threshold_stops_the_claim_with_no_model_call_and_no_draft(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(error=_not_expected)
    embedder = StubEmbedder(errors={1: _not_expected(None)})

    trace = _run(factory, primary, embedder=embedder, model=_model(0.29))

    assert trace.end_state is EndState.BELOW_THRESHOLD
    assert trace.rules.status is StepStatus.DONE
    assert (trace.scoring.status, trace.scoring.score, trace.scoring.threshold) == (
        StepStatus.STOPPED,
        0.29,
        THRESHOLD,
    )
    assert trace.retrieval == RetrievalStep(status=StepStatus.NOT_RUN)
    assert trace.drafting == DraftingStep(status=StepStatus.NOT_RUN)
    assert (primary.requests, embedder.requests) == ([], [])
    assert stored_drafts(factory) == {}


def test_a_score_equal_to_the_threshold_goes_on(factory: sessionmaker[Session]) -> None:
    trace = _run(factory, StubProvider(good_answer), model=_model(THRESHOLD))

    assert trace.end_state is EndState.NEEDS_REVIEW
    assert (trace.scoring.status, trace.scoring.score) == (StepStatus.DONE, THRESHOLD)


@pytest.mark.parametrize("threshold", [-0.01, 1.01, float("nan")])
def test_a_threshold_outside_0_to_1_is_refused_before_any_step(
    factory: sessionmaker[Session], threshold: float
) -> None:
    primary = StubProvider(error=_not_expected)

    with pytest.raises(ValueError, match="between 0 and 1"):
        _run(factory, primary, win_threshold=threshold)

    assert primary.requests == []


def test_a_blocked_draft_fails_names_its_kinds_of_problem_and_stores_nothing(
    factory: sessionmaker[Session],
) -> None:
    trace = _run(factory, StubProvider(two_problem_answer))

    assert trace.end_state is EndState.DRAFT_FAILED
    assert trace.retrieval.status is StepStatus.DONE
    assert trace.drafting == DraftingStep(
        status=StepStatus.FAILED,
        prompt_version=PROMPT_VERSION,
        failure=DraftFailure.BLOCKED,
        # Once each, in the order of the kinds, however often a problem was found.
        problem_kinds=(
            CitationProblemKind.POLICY_WITHOUT_CITATION,
            CitationProblemKind.QUOTE_NOT_IN_EVIDENCE,
        ),
    )
    assert stored_drafts(factory) == {}


def test_a_cut_off_answer_fails_the_draft_and_stores_nothing(
    factory: sessionmaker[Session],
) -> None:
    trace = _run(factory, StubProvider(good_answer, stop_reason=lambda request: "length"))

    assert trace.end_state is EndState.DRAFT_FAILED
    assert (trace.drafting.status, trace.drafting.failure, trace.drafting.problem_kinds) == (
        StepStatus.FAILED,
        DraftFailure.CUT_OFF,
        (),
    )
    assert stored_drafts(factory) == {}


def test_an_answer_that_cannot_be_parsed_fails_the_draft_and_stores_nothing(
    factory: sessionmaker[Session],
) -> None:
    trace = _run(factory, StubProvider(lambda request: "not JSON"))

    assert trace.end_state is EndState.DRAFT_FAILED
    assert (trace.drafting.status, trace.drafting.failure) == (
        StepStatus.FAILED,
        DraftFailure.NOT_PARSEABLE,
    )
    assert stored_drafts(factory) == {}


def test_a_draft_with_placeholder_text_fails_with_its_own_value_and_stores_nothing(
    factory: sessionmaker[Session],
) -> None:
    trace = _run(factory, StubProvider(placeholder_answer))

    assert trace.end_state is EndState.DRAFT_FAILED
    assert trace.retrieval.status is StepStatus.DONE
    assert trace.drafting == DraftingStep(
        status=StepStatus.FAILED,
        prompt_version=PROMPT_VERSION,
        failure=DraftFailure.PLACEHOLDER_TEXT,
    )
    assert stored_drafts(factory) == {}


def test_a_new_draft_replaces_the_one_the_claim_had(factory: sessionmaker[Session]) -> None:
    _run(factory, StubProvider(good_answer))

    trace = _run(factory, StubProvider(uncited_answer))

    assert trace.end_state is EndState.NEEDS_REVIEW
    assert trace.drafting.cited_paragraphs == 0
    drafts = stored_drafts(factory)
    assert set(drafts) == {CLAIM_1}
    letter = AppealLetter.model_validate(drafts[CLAIM_1].letter)
    assert [paragraph.text for paragraph in letter.paragraphs] == [OPENING_TEXT]


def test_a_failed_draft_leaves_the_stored_draft_as_it_was(
    factory: sessionmaker[Session],
) -> None:
    _run(factory, StubProvider(good_answer))

    trace = _run(factory, StubProvider(absent_quote_answer))

    assert trace.end_state is EndState.DRAFT_FAILED
    letter = AppealLetter.model_validate(stored_drafts(factory)[CLAIM_1].letter)
    assert letter.paragraphs[1].citations


def test_the_fallbacks_answer_is_named_in_the_trace(factory: sessionmaker[Session]) -> None:
    primary = StubProvider(error=lambda request: ProviderUnavailableError("timeout"))
    fallback = StubProvider(good_answer, model_name="example-fallback-model")

    trace = _run(factory, primary, fallback=fallback)

    assert (trace.drafting.provider, trace.drafting.model_name) == (
        LlmProvider.FALLBACK,
        "example-fallback-model",
    )


@pytest.mark.parametrize(
    "answer", [good_answer, uncited_answer, absent_quote_answer, two_problem_answer]
)
def test_the_trace_holds_no_text_and_no_extracted_value(
    factory: sessionmaker[Session], answer: Any
) -> None:
    trace = _run(factory, StubProvider(answer))

    dumped = trace.model_dump_json()
    for forbidden in (
        PATIENT_NAME,  # an extracted value
        CLAIM_1,  # the letter's claim number
        "MBR-000001",
        "Made Up",
        "130.10",
        "99213",
        "2008-03-20",  # the letter date
        "2008-09-16",  # the appeal deadline
        CHUNK_TEXT,  # evidence text
        SEARCH_PHRASES[DenialReasonCategory.NONCOVERED],  # the search question
        OPENING_TEXT,  # letter text
        POLICY_TEXT,
        ABSENT_QUOTE,  # a quote
        document_text(CLAIM_1, LETTER),  # document text
    ):
        assert forbidden not in dumped


def test_a_reached_budget_passes_through_with_no_draft(factory: sessionmaker[Session]) -> None:
    primary = StubProvider(good_answer, highest_cost="5.00")

    with pytest.raises(LlmBudgetExceededError):
        _run(factory, primary)

    assert primary.requests == []
    assert stored_drafts(factory) == {}


def test_no_provider_able_to_answer_passes_through_with_no_draft(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(error=lambda request: ProviderUnavailableError("timeout"))

    with pytest.raises(LlmUnavailableError):
        _run(factory, primary)

    assert stored_drafts(factory) == {}


def test_a_refused_request_passes_through_with_no_draft(factory: sessionmaker[Session]) -> None:
    primary = StubProvider(
        error=lambda request: ProviderRejectedError("BadRequestError (status 400)")
    )

    with pytest.raises(ProviderRejectedError):
        _run(factory, primary)

    assert stored_drafts(factory) == {}


def test_evidence_without_a_vector_passes_through_before_any_call(engine: Engine) -> None:
    primary = StubProvider(error=_not_expected)
    embedder = StubEmbedder()

    with drafting_data(engine, embedded=False) as factory:
        with pytest.raises(EvidenceNotEmbeddedError):
            _run(factory, primary, embedder=embedder)

        assert (primary.requests, embedder.requests) == ([], [])
        assert stored_drafts(factory) == {}


def test_a_question_vector_of_another_model_passes_through_with_no_draft(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(error=_not_expected)

    with pytest.raises(QueryEmbeddingError):
        _run(factory, primary, embedder=StubEmbedder("example-other-embedding-model"))

    assert primary.requests == []
    assert stored_drafts(factory) == {}


def test_a_search_answer_without_its_vector_passes_through_with_no_draft(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(error=_not_expected)

    with pytest.raises(EmbeddingAnswerError):
        _run(factory, primary, embedder=StubEmbedder(short_by={1: 1}))

    assert primary.requests == []
    assert stored_drafts(factory) == {}


def test_nothing_in_the_agent_package_reads_the_clock() -> None:
    sources = sorted(Path(src.agent.__file__).parent.glob("*.py"))

    assert [path.name for path in sources] == [
        "__init__.py",
        "loop.py",
        "run.py",
        "store.py",
        "trace.py",
    ]
    for path in sources:
        assert not CLOCK_READ.search(path.read_text(encoding="utf-8")), path.name
