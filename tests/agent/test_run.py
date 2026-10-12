import logging
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.agent.loop import AgentCandidate
from src.agent.run import AgentRunSummary, run_agent
from src.agent.store import select_agent_candidates
from src.agent.trace import AGENT_VERSION, AgentTrace, DraftFailure, StepStatus
from src.db.models import DatasetSplit, EndState, LlmProvider
from src.db.session import session_scope
from src.drafting.run import (
    BUDGET_REACHED,
    EVIDENCE_NOT_EMBEDDED,
    NO_PROVIDER,
    REQUEST_REFUSED,
    SEARCH_ANSWER_NOT_USABLE,
)
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.extraction.store import text_sha256
from src.llm.gateway import LlmRequest, ProviderRejectedError, ProviderUnavailableError
from src.ml.training import WinModel
from tests.agent.helpers import agent_data, change_letter, fixed_model, stored_runs
from tests.drafting.helpers import (
    ABSENT_QUOTE,
    OPENING_TEXT,
    PROMPT,
    absent_quote_answer,
    drafting_gateway,
    good_answer,
    is_for,
    placeholder_answer,
    stored_drafts,
    two_problem_answer,
    uncited_answer,
    wrong_amount_answer,
)
from tests.extraction.helpers import (
    CLAIM_1,
    CLAIM_2,
    LETTER,
    PATIENT_NAME,
    StubProvider,
    document_text,
)
from tests.rag.helpers import CHUNK_TEXT, MODEL, StubEmbedder

# The made-up letter is dated 2008-03-20, may be appealed until 2008-09-16 and is about 130.10.
BEFORE_THE_DEADLINE = date(2008, 6, 1)
AFTER_THE_DEADLINE = date(2009, 1, 1)


def _not_expected(request: object) -> Exception:
    return AssertionError("no model call is expected for this claim")


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with agent_data(engine) as session_factory:
        yield session_factory


def _validation(factory: sessionmaker[Session]) -> list[AgentCandidate]:
    with session_scope(factory) as session:
        return select_agent_candidates(session, DatasetSplit.VALIDATION, EXTRACTION_PROMPT_VERSION)


def _run(
    factory: sessionmaker[Session],
    primary: StubProvider,
    *,
    fallback: StubProvider | None = None,
    embedder: StubEmbedder | None = None,
    candidates: list[AgentCandidate] | None = None,
    model: WinModel | None = None,
    today: date = BEFORE_THE_DEADLINE,
) -> AgentRunSummary:
    return run_agent(
        factory,
        drafting_gateway(factory, primary, fallback, embedder),
        PROMPT,
        model or fixed_model(),
        _validation(factory) if candidates is None else candidates,
        today=today,
        amount_floor=Decimal("25"),
        win_threshold=0.30,
        embedding_model=MODEL,
    )


def _counts(**changed: int) -> dict[str, int]:
    return {state.value: changed.get(state.value, 0) for state in EndState}


def _end_states(factory: sessionmaker[Session]) -> dict[str, EndState]:
    return {claim_id: row.end_state for claim_id, row in stored_runs(factory).items()}


def test_each_claims_trace_is_stored_and_counted_by_end_state(
    factory: sessionmaker[Session],
) -> None:
    change_letter(factory, CLAIM_2, appeal_deadline=None)
    primary = StubProvider(good_answer)
    embedder = StubEmbedder()

    summary = _run(factory, primary, embedder=embedder)

    assert summary == AgentRunSummary(
        selected=2,
        already_finished=0,
        end_states=_counts(needs_review=1, needs_review_no_draft=1),
        drafted_with_citation=1,
        draft_failures={},
        rules_failed=0,
        answered_by_fallback=0,
        stop_reason=None,
    )
    assert (summary.stored, summary.not_tried) == (2, 0)
    # Only the claim the rules let through is paid for.
    assert (len(embedder.requests), len(primary.requests)) == (1, 1)
    assert _end_states(factory) == {
        CLAIM_1: EndState.NEEDS_REVIEW,
        CLAIM_2: EndState.NEEDS_REVIEW_NO_DRAFT,
    }
    assert set(stored_drafts(factory)) == {CLAIM_1}


def test_a_stored_row_holds_the_whole_trace_and_its_columns_repeat_it(
    factory: sessionmaker[Session],
) -> None:
    _run(factory, StubProvider(good_answer))

    row = stored_runs(factory)[CLAIM_1]
    trace = AgentTrace.model_validate(row.trace)
    assert (row.agent_version, row.end_state) == (AGENT_VERSION, trace.end_state)
    assert row.text_sha256 == trace.extraction.text_sha256
    assert row.text_sha256 == text_sha256(document_text(CLAIM_1, LETTER))
    assert trace.rules.today == BEFORE_THE_DEADLINE
    assert trace.rules.amount_floor == Decimal("25")
    assert (trace.scoring.score, trace.scoring.threshold) == (0.75, 0.30)
    assert trace.drafting.status is StepStatus.DONE


def test_claims_the_rules_stop_are_stored_and_cost_nothing(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(error=_not_expected)
    embedder = StubEmbedder(errors={1: _not_expected(None)})

    summary = _run(factory, primary, embedder=embedder, today=AFTER_THE_DEADLINE)

    assert summary.end_states == _counts(stopped_by_rules=2)
    assert (primary.requests, embedder.requests) == ([], [])
    assert set(_end_states(factory).values()) == {EndState.STOPPED_BY_RULES}
    assert stored_drafts(factory) == {}


def test_claims_below_the_threshold_are_stored_and_cost_nothing(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(error=_not_expected)

    summary = _run(factory, primary, model=fixed_model(0.29))

    assert summary.end_states == _counts(below_threshold=2)
    assert primary.requests == []
    assert set(_end_states(factory).values()) == {EndState.BELOW_THRESHOLD}


def test_failed_drafts_are_stored_counted_by_kind_and_the_run_goes_on(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(
        lambda request: two_problem_answer(request) if is_for(CLAIM_1)(request) else "not JSON"
    )

    summary = _run(factory, primary)

    assert summary.end_states == _counts(draft_failed=2)
    assert summary.draft_failures == {
        DraftFailure.BLOCKED.value: 1,
        DraftFailure.NOT_PARSEABLE.value: 1,
    }
    assert (summary.rules_failed, summary.stop_reason, summary.not_tried) == (0, None, 0)
    assert set(_end_states(factory).values()) == {EndState.DRAFT_FAILED}
    assert stored_drafts(factory) == {}


def test_a_cut_off_answer_and_an_uncited_letter_are_counted(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(
        uncited_answer,
        stop_reason=lambda request: "length" if is_for(CLAIM_1)(request) else "stop",
    )

    summary = _run(factory, primary)

    assert summary.end_states == _counts(draft_failed=1, needs_review=1)
    assert summary.draft_failures == {DraftFailure.CUT_OFF.value: 1}
    assert summary.drafted_with_citation == 0


def test_a_claim_with_no_rules_verdict_is_counted_apart_from_the_failed_drafts(
    factory: sessionmaker[Session],
) -> None:
    change_letter(factory, CLAIM_1, total_allowed_charge_amount="-1.00")

    summary = _run(factory, StubProvider(good_answer))

    assert summary.end_states == _counts(draft_failed=1, needs_review=1)
    assert (summary.rules_failed, summary.draft_failures) == (1, {})


def test_a_finished_claim_is_skipped_and_costs_nothing(factory: sessionmaker[Session]) -> None:
    change_letter(factory, CLAIM_2, appeal_deadline=None)
    _run(factory, StubProvider(good_answer))
    before = {claim_id: row.updated_at for claim_id, row in stored_runs(factory).items()}
    primary = StubProvider(error=_not_expected)
    embedder = StubEmbedder(errors={1: _not_expected(None)})

    # A date that would stop both claims: a finished claim is not looked at again.
    summary = _run(factory, primary, embedder=embedder, today=AFTER_THE_DEADLINE)

    assert (summary.selected, summary.already_finished, summary.stored) == (2, 2, 0)
    assert (summary.not_tried, summary.stop_reason) == (0, None)
    assert (primary.requests, embedder.requests) == ([], [])
    assert {claim_id: row.updated_at for claim_id, row in stored_runs(factory).items()} == before


def test_a_claim_whose_draft_failed_is_taken_again_and_its_trace_replaced(
    factory: sessionmaker[Session],
) -> None:
    first = _run(factory, StubProvider(absent_quote_answer))
    assert first.end_states == _counts(draft_failed=2)

    second = _run(factory, StubProvider(good_answer))

    assert (second.already_finished, second.end_states) == (0, _counts(needs_review=2))
    runs = stored_runs(factory)
    assert {claim_id: row.end_state for claim_id, row in runs.items()} == {
        CLAIM_1: EndState.NEEDS_REVIEW,
        CLAIM_2: EndState.NEEDS_REVIEW,
    }
    assert set(stored_drafts(factory)) == {CLAIM_1, CLAIM_2}


def test_a_claim_refused_for_placeholder_text_is_counted_and_taken_again_by_the_next_run(
    factory: sessionmaker[Session],
) -> None:
    first = _run(
        factory,
        StubProvider(
            lambda request: (
                placeholder_answer(request) if is_for(CLAIM_1)(request) else good_answer(request)
            )
        ),
    )
    assert first.end_states == _counts(draft_failed=1, needs_review=1)
    assert first.draft_failures == {DraftFailure.PLACEHOLDER_TEXT.value: 1}
    assert set(stored_drafts(factory)) == {CLAIM_2}

    primary = StubProvider(good_answer)
    second = _run(factory, primary)

    assert (second.already_finished, second.end_states) == (1, _counts(needs_review=1))
    assert [is_for(CLAIM_1)(request) for request in primary.requests] == [True]
    assert set(stored_drafts(factory)) == {CLAIM_1, CLAIM_2}


def test_a_claim_refused_by_the_fact_check_is_counted_and_taken_again_by_the_next_run(
    factory: sessionmaker[Session],
) -> None:
    first = _run(
        factory,
        StubProvider(
            lambda request: (
                wrong_amount_answer(request) if is_for(CLAIM_1)(request) else good_answer(request)
            )
        ),
    )
    assert first.end_states == _counts(draft_failed=1, needs_review=1)
    assert first.draft_failures == {DraftFailure.FACT_CHECK.value: 1}
    assert set(stored_drafts(factory)) == {CLAIM_2}

    primary = StubProvider(good_answer)
    second = _run(factory, primary)

    assert (second.already_finished, second.end_states) == (1, _counts(needs_review=1))
    assert [is_for(CLAIM_1)(request) for request in primary.requests] == [True]
    assert set(stored_drafts(factory)) == {CLAIM_1, CLAIM_2}


def test_the_run_stops_when_the_budget_is_reached_and_stores_no_trace(
    factory: sessionmaker[Session],
) -> None:
    primary = StubProvider(good_answer, highest_cost="5.00")

    summary = _run(factory, primary)

    assert summary.stop_reason == BUDGET_REACHED
    assert (summary.stored, summary.not_tried) == (0, 2)
    assert primary.requests == []
    assert stored_runs(factory) == {}


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
    assert (summary.stored, summary.not_tried) == (1, 1)
    assert _end_states(factory) == {CLAIM_1: EndState.NEEDS_REVIEW}


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
    assert stored_runs(factory) == {}


@pytest.mark.parametrize(
    "embedder",
    [StubEmbedder("example-other-embedding-model"), StubEmbedder(short_by={1: 1})],
    ids=["a vector of another model", "a missing vector"],
)
def test_the_run_stops_when_the_search_answer_cannot_be_used(
    factory: sessionmaker[Session], embedder: StubEmbedder
) -> None:
    primary = StubProvider(error=_not_expected)

    summary = _run(factory, primary, embedder=embedder)

    assert summary.stop_reason is not None
    assert summary.stop_reason.startswith(f"{SEARCH_ANSWER_NOT_USABLE}: ")
    # One paid question, then the run ends: the second claim would fail the same way.
    assert len(embedder.requests) == 1
    assert primary.requests == []
    assert summary.not_tried == 2
    assert stored_runs(factory) == {}


def test_the_run_stops_before_any_call_when_the_evidence_has_no_vector(engine: Engine) -> None:
    primary = StubProvider(error=_not_expected)
    embedder = StubEmbedder()

    with agent_data(engine, embedded=False) as factory:
        summary = _run(factory, primary, embedder=embedder)
        runs = stored_runs(factory)

    assert summary.stop_reason is not None
    assert summary.stop_reason.startswith(f"{EVIDENCE_NOT_EMBEDDED}: ")
    assert (primary.requests, embedder.requests) == ([], [])
    assert (summary.not_tried, runs) == (2, {})


def test_the_fallbacks_answer_is_counted(factory: sessionmaker[Session]) -> None:
    primary = StubProvider(error=lambda request: ProviderUnavailableError("timeout"))
    fallback = StubProvider(good_answer, model_name="example-fallback-model")

    summary = _run(factory, primary, fallback=fallback)

    assert (summary.end_states, summary.answered_by_fallback) == (_counts(needs_review=2), 2)
    trace = AgentTrace.model_validate(stored_runs(factory)[CLAIM_1].trace)
    assert trace.drafting.provider is LlmProvider.FALLBACK


def test_an_empty_list_of_claims_makes_no_call(factory: sessionmaker[Session]) -> None:
    primary = StubProvider(error=_not_expected)

    summary = _run(factory, primary, candidates=[])

    assert (summary.selected, summary.stored, summary.not_tried) == (0, 0, 0)
    assert summary.end_states == _counts()
    assert primary.requests == []


def test_a_threshold_outside_0_to_1_is_refused_and_nothing_is_stored(
    factory: sessionmaker[Session],
) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        run_agent(
            factory,
            drafting_gateway(factory, StubProvider(error=_not_expected)),
            PROMPT,
            fixed_model(),
            _validation(factory),
            today=BEFORE_THE_DEADLINE,
            amount_floor=Decimal("25"),
            win_threshold=1.5,
            embedding_model=MODEL,
        )

    assert stored_runs(factory) == {}


def test_progress_is_logged_with_counts_and_no_letter_or_evidence_text(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("src.agent.run.PROGRESS_EVERY", 1)
    caplog.set_level(logging.DEBUG)

    def answer(request: LlmRequest) -> str:
        return absent_quote_answer(request) if is_for(CLAIM_1)(request) else good_answer(request)

    _run(factory, StubProvider(answer))

    assert (
        "agent progress: 2 of 2 claims (1 with a draft, 0 stopped before a draft, 1 failed)"
        in caplog.text
    )
    for hidden in (PATIENT_NAME, CHUNK_TEXT, ABSENT_QUOTE, OPENING_TEXT, "MBR-000001", CLAIM_1):
        assert hidden not in caplog.text
