from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, delete, update
from sqlalchemy.orm import Session, sessionmaker

from src.agent.loop import AgentCandidate
from src.agent.store import (
    ClaimRowsError,
    build_agent_run_row,
    read_finished_claim_ids,
    save_agent_run,
    select_agent_candidates,
)
from src.agent.trace import AGENT_VERSION, AgentTrace, ExtractionStep, RulesStep, StepStatus
from src.db.models import (
    ClaimSampleLabel,
    ClaimSampleLine,
    DatasetSplit,
    DenialReasonCategory,
    EndState,
)
from src.db.session import session_scope
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.extraction.store import text_sha256
from src.ml.features import ClaimFeatures
from src.rules.verdict import RULES_VERSION
from tests.agent.helpers import add_claim_line, agent_data, fit_label, stored_runs
from tests.extraction.helpers import CLAIM_1, CLAIM_2, CLAIM_3, LETTER, document_text


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with agent_data(engine) as session_factory:
        yield session_factory


def _validation(factory: sessionmaker[Session], limit: int | None = None) -> list[AgentCandidate]:
    with session_scope(factory) as session:
        return select_agent_candidates(
            session, DatasetSplit.VALIDATION, EXTRACTION_PROMPT_VERSION, limit
        )


def _trace(end_state: EndState, agent_version: str = AGENT_VERSION) -> AgentTrace:
    return AgentTrace(
        end_state=end_state,
        extraction=ExtractionStep(
            status=StepStatus.DONE,
            prompt_version=EXTRACTION_PROMPT_VERSION,
            text_sha256=text_sha256(document_text(CLAIM_1, LETTER)),
        ),
        rules=RulesStep(
            status=StepStatus.STOPPED,
            rules_version=RULES_VERSION,
            today=date(2010, 1, 1),
            amount_floor=Decimal("25"),
        ),
        agent_version=agent_version,
    )


def _save(factory: sessionmaker[Session], candidate: AgentCandidate, trace: AgentTrace) -> None:
    with session_scope(factory) as session:
        save_agent_run(session, build_agent_run_row(candidate, trace))


def test_a_candidate_carries_the_letter_and_the_features_of_its_claim_rows(
    factory: sessionmaker[Session],
) -> None:
    first, second = _validation(factory)

    assert (first.source_claim_id, second.source_claim_id) == (CLAIM_1, CLAIM_2)
    assert first.extraction_prompt_version == EXTRACTION_PROMPT_VERSION
    assert first.text_sha256 == text_sha256(document_text(CLAIM_1, LETTER))
    assert first.letter.claim_number.value == CLAIM_1
    assert first.features == ClaimFeatures(
        denial_reason_category=DenialReasonCategory.NONCOVERED,
        total_allowed_charge=Decimal("130.10"),
        fully_denied=True,
    )


def test_the_features_count_every_line_of_the_claim_not_only_the_denied_ones(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        add_claim_line(session, CLAIM_2, 2, indicator="A", payment="40.00", allowed="50.00")
        fit_label(session, CLAIM_2)

    features = _validation(factory)[1].features

    # The letter lists the denied line only (130.10); the claim rows give the whole claim.
    assert (features.total_allowed_charge, features.fully_denied) == (Decimal("180.10"), False)


def test_a_limit_takes_the_first_claims_in_claim_id_order(
    factory: sessionmaker[Session],
) -> None:
    assert [candidate.source_claim_id for candidate in _validation(factory, 1)] == [CLAIM_1]


def test_the_split_chooses_the_claims(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as session:
        test_split = select_agent_candidates(session, DatasetSplit.TEST, EXTRACTION_PROMPT_VERSION)
        train_split = select_agent_candidates(
            session, DatasetSplit.TRAIN, EXTRACTION_PROMPT_VERSION
        )

    assert [candidate.source_claim_id for candidate in test_split] == [CLAIM_3]
    assert train_split == []


def test_a_claim_with_no_lines_is_refused_without_naming_the_claim(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        session.execute(delete(ClaimSampleLine).where(ClaimSampleLine.source_claim_id == CLAIM_2))

    with pytest.raises(ClaimRowsError, match="1 of the 2 selected claims") as raised:
        _validation(factory)

    assert "pipelines.run_data_pipeline again" in str(raised.value)
    assert CLAIM_2 not in str(raised.value)


@pytest.mark.parametrize(
    "changed",
    [
        {"denial_reason_category": DenialReasonCategory.DUPLICATE},
        {"label_rule_version": "v1"},
    ],
    ids=["another category", "an older label rule"],
)
def test_a_label_that_no_longer_fits_the_lines_is_refused(
    factory: sessionmaker[Session], changed: dict[str, object]
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(ClaimSampleLabel)
            .where(ClaimSampleLabel.source_claim_id == CLAIM_1)
            .values(**changed)
        )

    with pytest.raises(ClaimRowsError, match="no longer fits"):
        _validation(factory)


def test_lines_loaded_again_with_another_reason_are_refused(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        session.execute(
            update(ClaimSampleLine)
            .where(ClaimSampleLine.source_claim_id == CLAIM_1)
            .values(processing_indicator="M")
        )

    with pytest.raises(ClaimRowsError):
        _validation(factory)


def test_a_stale_claim_outside_the_selection_does_not_refuse_it(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as session:
        session.execute(delete(ClaimSampleLine).where(ClaimSampleLine.source_claim_id == CLAIM_2))

    assert [candidate.source_claim_id for candidate in _validation(factory, 1)] == [CLAIM_1]


def test_a_stored_trace_reads_back_as_the_same_trace(factory: sessionmaker[Session]) -> None:
    candidate = _validation(factory)[0]
    trace = _trace(EndState.STOPPED_BY_RULES)

    _save(factory, candidate, trace)

    stored = stored_runs(factory)[CLAIM_1]
    assert (stored.agent_version, stored.end_state) == (AGENT_VERSION, EndState.STOPPED_BY_RULES)
    assert stored.text_sha256 == trace.extraction.text_sha256
    assert AgentTrace.model_validate(stored.trace) == trace


def test_a_new_trace_replaces_the_one_the_claim_and_agent_version_had(
    factory: sessionmaker[Session],
) -> None:
    candidate = _validation(factory)[0]
    _save(factory, candidate, _trace(EndState.DRAFT_FAILED))
    first_id = stored_runs(factory)[CLAIM_1].id

    _save(factory, candidate, _trace(EndState.NEEDS_REVIEW_NO_DRAFT))

    stored = stored_runs(factory)
    assert set(stored) == {CLAIM_1}
    assert (stored[CLAIM_1].id, stored[CLAIM_1].end_state) == (
        first_id,
        EndState.NEEDS_REVIEW_NO_DRAFT,
    )
    assert stored[CLAIM_1].trace["end_state"] == "needs_review_no_draft"


def test_every_end_state_is_final_except_a_failed_draft(factory: sessionmaker[Session]) -> None:
    first, second = _validation(factory)
    _save(factory, first, _trace(EndState.DRAFT_FAILED))

    for end_state in EndState:
        _save(factory, second, _trace(end_state))
        with session_scope(factory) as session:
            finished = read_finished_claim_ids(session, AGENT_VERSION)
        assert (CLAIM_2 in finished) is (end_state is not EndState.DRAFT_FAILED), end_state
        assert CLAIM_1 not in finished


@pytest.mark.parametrize("earlier_version", ["v1", "v2"])
def test_a_trace_of_an_earlier_agent_version_does_not_finish_a_claim(
    factory: sessionmaker[Session], earlier_version: str
) -> None:
    candidate = _validation(factory)[0]
    _save(factory, candidate, _trace(EndState.STOPPED_BY_RULES, agent_version=earlier_version))

    with session_scope(factory) as session:
        assert CLAIM_1 not in read_finished_claim_ids(session, AGENT_VERSION)
        assert CLAIM_1 in read_finished_claim_ids(session, earlier_version)
