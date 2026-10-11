"""Walks one denied claim through the five steps and returns the trace of what happened.

The order is fixed in code: the stored letter extraction, the rules, the win model, the
evidence search, the draft. Each step runs at most once and no model picks the next step.
Only a `pass` verdict and a score at or above the threshold reach the two paid steps, so a
claim costs at most one embedding call and one model call.

The loop always ends in a stop for a person. Nothing here sends a letter anywhere, and
nothing reads the clock: "today", the amount floor and the threshold are passed in.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from src.agent.trace import (
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
from src.db.session import session_scope
from src.drafting.citation_validator import CitationProblemKind
from src.drafting.drafter import (
    DraftBlockedError,
    DraftingError,
    DraftTruncatedError,
    draft_appeal,
)
from src.drafting.prompts import DraftingPrompt
from src.drafting.search_question import SEARCH_QUESTION_VERSION, build_search_question
from src.drafting.store import DraftCandidate, build_appeal_draft_row, save_appeal_draft
from src.extraction.schemas import DenialLetterExtraction
from src.llm.gateway import LlmGateway
from src.ml.features import FEATURE_VERSION, ClaimFeatures
from src.ml.inference import predict_win_probability
from src.ml.training import MODEL_VERSION, WinModel
from src.rag.retrieval import search_evidence
from src.rules.evaluate import evaluate_rules
from src.rules.verdict import RULES_VERSION, RuleInputs, RuleOutcome

# The end state of a claim the rules do not let through.
RULES_END_STATES = {
    RuleOutcome.MUST_REVIEW: EndState.NEEDS_REVIEW_NO_DRAFT,
    RuleOutcome.HARD_FAIL: EndState.STOPPED_BY_RULES,
}


class AgentCandidate(DraftCandidate):
    """One denied claim the loop can take: a draft candidate, plus what the other steps need.

    `extraction_prompt_version` is the version of the stored extraction `letter` was read
    from. `features` are the model inputs, built from the claim's stored rows.
    """

    extraction_prompt_version: str
    features: ClaimFeatures


def run_agent_for_claim(
    session_factory: sessionmaker[Session],
    gateway: LlmGateway,
    prompt: DraftingPrompt,
    model: WinModel,
    candidate: AgentCandidate,
    *,
    today: date,
    amount_floor: Decimal,
    win_threshold: float,
    embedding_model: str,
) -> AgentTrace:
    """Run the loop for `candidate` and return its trace.

    Spends money and can take several seconds when the claim reaches the last two steps: one
    embedding call and one model call through `gateway`. A claim the rules or the threshold
    stop costs nothing. `embedding_model` must be the model the gateway embeds with.

    A checked letter is stored in `appeal_drafts`, in its own commit, replacing the draft the
    claim had for the prompt version. The trace is returned, not stored. A draft that is
    blocked, cut off or not parseable ends as `draft_failed` and stores nothing.

    Errors that say nothing about the claim pass through, with no trace: the gateway's
    (`LlmBudgetExceededError`, `LlmUnavailableError`, `ProviderRejectedError`,
    `EmbeddingAnswerError`) and the search's (`EvidenceNotEmbeddedError`,
    `QueryEmbeddingError`). Raises `ValueError` when `win_threshold` is not between 0 and 1.
    """
    if not 0 <= win_threshold <= 1:
        raise ValueError("the win threshold must be between 0 and 1")
    letter = candidate.letter
    extraction = ExtractionStep(
        status=StepStatus.DONE,
        prompt_version=candidate.extraction_prompt_version,
        text_sha256=candidate.text_sha256,
    )

    rules = _rules_step(letter, today, amount_floor)
    if rules.outcome is not RuleOutcome.PASS:
        end_state = (
            EndState.DRAFT_FAILED if rules.outcome is None else RULES_END_STATES[rules.outcome]
        )
        return AgentTrace(end_state=end_state, extraction=extraction, rules=rules)

    score = predict_win_probability(model, candidate.features)
    scoring = ScoringStep(
        status=StepStatus.DONE if score >= win_threshold else StepStatus.STOPPED,
        feature_version=FEATURE_VERSION,
        model_version=MODEL_VERSION,
        score=score,
        threshold=win_threshold,
    )
    if scoring.status is StepStatus.STOPPED:
        return AgentTrace(
            end_state=EndState.BELOW_THRESHOLD, extraction=extraction, rules=rules, scoring=scoring
        )

    question = build_search_question(
        letter.denial_reason_category.value,
        [
            line.hcpcs_code
            for line in letter.denied_lines.value or ()
            if line.hcpcs_code is not None
        ],
    )
    with session_scope(session_factory) as session:
        matches = search_evidence(session, gateway, question, model_name=embedding_model)
    retrieval = RetrievalStep(
        status=StepStatus.DONE,
        search_question_version=SEARCH_QUESTION_VERSION,
        embedding_model=embedding_model,
        matches=tuple(
            EvidenceReference(
                source=match.source,
                source_document_id=match.source_document_id,
                chunk_index=match.chunk_index,
                text_sha256=match.text_sha256,
                similarity=match.similarity,
            )
            for match in matches
        ),
    )

    try:
        result = draft_appeal(gateway, prompt, letter, matches)
    except DraftingError as error:
        return AgentTrace(
            end_state=EndState.DRAFT_FAILED,
            extraction=extraction,
            rules=rules,
            scoring=scoring,
            retrieval=retrieval,
            drafting=_failed_drafting_step(prompt, error),
        )
    with session_scope(session_factory) as session:
        save_appeal_draft(session, build_appeal_draft_row(candidate, result))
    return AgentTrace(
        end_state=EndState.NEEDS_REVIEW,
        extraction=extraction,
        rules=rules,
        scoring=scoring,
        retrieval=retrieval,
        drafting=DraftingStep(
            status=StepStatus.DONE,
            prompt_version=result.prompt_version,
            provider=result.provider,
            model_name=result.model_name,
            cited_paragraphs=sum(
                1 for paragraph in result.letter.paragraphs if paragraph.citations
            ),
        ),
    )


def _rules_step(letter: DenialLetterExtraction, today: date, amount_floor: Decimal) -> RulesStep:
    amount = letter.total_allowed_charge_amount.value
    if amount is not None and amount < 0:
        # The rules refuse an amount below zero, so there is no verdict for this claim.
        return RulesStep(
            status=StepStatus.FAILED,
            rules_version=RULES_VERSION,
            today=today,
            amount_floor=amount_floor,
        )
    verdict = evaluate_rules(
        RuleInputs(
            letter_date=letter.letter_date.value,
            appeal_deadline=letter.appeal_deadline.value,
            total_allowed_charge_amount=amount,
        ),
        today=today,
        amount_floor=amount_floor,
    )
    return RulesStep(
        status=StepStatus.DONE if verdict.outcome is RuleOutcome.PASS else StepStatus.STOPPED,
        rules_version=verdict.rules_version,
        today=today,
        amount_floor=amount_floor,
        outcome=verdict.outcome,
        reasons=verdict.reasons,
    )


def _failed_drafting_step(prompt: DraftingPrompt, error: DraftingError) -> DraftingStep:
    problem_kinds: tuple[CitationProblemKind, ...] = ()
    if isinstance(error, DraftBlockedError):
        failure = DraftFailure.BLOCKED
        found = {problem.kind for problem in error.problems}
        problem_kinds = tuple(kind for kind in CitationProblemKind if kind in found)
    elif isinstance(error, DraftTruncatedError):
        failure = DraftFailure.CUT_OFF
    else:
        failure = DraftFailure.NOT_PARSEABLE
    return DraftingStep(
        status=StepStatus.FAILED,
        prompt_version=prompt.version,
        failure=failure,
        problem_kinds=problem_kinds,
    )
