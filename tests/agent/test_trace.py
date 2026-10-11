from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.agent.trace import (
    AGENT_VERSION,
    AgentTrace,
    DraftFailure,
    DraftingStep,
    EndState,
    EvidenceReference,
    ExtractionStep,
    RetrievalStep,
    RulesStep,
    ScoringStep,
    StepStatus,
)
from src.db.models import EvidenceSource
from src.rules.verdict import RULES_VERSION, RuleOutcome, RuleReason

MADE_UP_HASH = "ab" * 32
EXTRACTION = ExtractionStep(status=StepStatus.DONE, prompt_version="v2", text_sha256=MADE_UP_HASH)
RULES = RulesStep(
    status=StepStatus.STOPPED,
    rules_version=RULES_VERSION,
    today=date(2010, 1, 1),
    amount_floor=Decimal("25"),
    outcome=RuleOutcome.HARD_FAIL,
    reasons=(RuleReason.DEADLINE_PASSED,),
)


def test_the_agent_version_is_v1() -> None:
    assert AGENT_VERSION == "v1"


def test_a_step_is_done_stopped_failed_or_not_run() -> None:
    assert [status.value for status in StepStatus] == ["done", "stopped", "failed", "not_run"]


def test_there_are_five_end_states_and_none_means_approved_or_sent() -> None:
    assert [state.value for state in EndState] == [
        "needs_review",
        "needs_review_no_draft",
        "stopped_by_rules",
        "below_threshold",
        "draft_failed",
    ]


def test_a_draft_fails_in_one_of_three_ways() -> None:
    assert [failure.value for failure in DraftFailure] == ["blocked", "cut_off", "not_parseable"]


def test_the_steps_a_trace_is_not_given_are_recorded_as_not_run() -> None:
    trace = AgentTrace(end_state=EndState.STOPPED_BY_RULES, extraction=EXTRACTION, rules=RULES)

    assert trace.agent_version == AGENT_VERSION
    assert trace.scoring == ScoringStep(status=StepStatus.NOT_RUN)
    assert trace.retrieval == RetrievalStep(status=StepStatus.NOT_RUN)
    assert trace.drafting == DraftingStep(status=StepStatus.NOT_RUN)
    assert (trace.scoring.score, trace.retrieval.matches, trace.drafting.provider) == (
        None,
        (),
        None,
    )


def test_a_trace_reads_back_from_its_json_values() -> None:
    trace = AgentTrace(
        end_state=EndState.BELOW_THRESHOLD,
        extraction=EXTRACTION,
        rules=RULES,
        scoring=ScoringStep(
            status=StepStatus.STOPPED,
            feature_version="v1",
            model_version="v1",
            score=0.25,
            threshold=0.3,
        ),
        retrieval=RetrievalStep(
            status=StepStatus.DONE,
            search_question_version="v1",
            embedding_model="example-embedding-model",
            matches=(
                EvidenceReference(
                    source=EvidenceSource.CMS_NCD,
                    source_document_id="1",
                    chunk_index=0,
                    text_sha256=MADE_UP_HASH,
                    similarity=0.5,
                ),
            ),
        ),
    )

    assert AgentTrace.model_validate(trace.model_dump(mode="json")) == trace


def test_a_trace_cannot_be_changed_and_takes_no_unknown_field() -> None:
    trace = AgentTrace(end_state=EndState.STOPPED_BY_RULES, extraction=EXTRACTION, rules=RULES)

    with pytest.raises(ValidationError):
        trace.end_state = EndState.NEEDS_REVIEW
    with pytest.raises(ValidationError):
        AgentTrace.model_validate({**trace.model_dump(), "letter_text": "made-up text"})
    with pytest.raises(ValidationError):
        ScoringStep.model_validate({"status": "done", "question": "made-up question"})


def test_an_end_state_outside_the_five_is_refused() -> None:
    with pytest.raises(ValidationError):
        AgentTrace.model_validate(
            {"end_state": "approved", "extraction": EXTRACTION, "rules": RULES}
        )


@pytest.mark.parametrize("field", ["score", "threshold"])
@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_a_score_or_threshold_outside_0_to_1_is_refused(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        ScoringStep.model_validate({"status": "done", field: value})


def test_the_letter_hash_must_be_a_sha256() -> None:
    with pytest.raises(ValidationError):
        ExtractionStep(status=StepStatus.DONE, prompt_version="v2", text_sha256="made-up text")
