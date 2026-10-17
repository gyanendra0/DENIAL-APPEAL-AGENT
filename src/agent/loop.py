"""Walks one denied claim through the five steps and returns the trace of what happened.

The order is fixed in code: the stored letter extraction, the rules, the win model, the
evidence search, the draft. Each step runs at most once and no model picks the next step;
only the drafting call is made a second time, with the same evidence, when the first draft
is blocked or cannot be parsed. Only a `pass` verdict and a score at or above the threshold
reach the two paid steps, so a claim costs at most one embedding call and two model calls.

The loop always ends in a stop for a person. Nothing here sends a letter anywhere, and
nothing reads the clock: "today", the amount floor and the threshold are passed in. The loop
stores nothing either: it returns the trace and the checked letter, and the caller decides
where they go (`appeal_drafts` for a sample claim, the account's tables for an upload).
"""

from datetime import date
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from src.agent.trace import (
    MAX_DRAFT_ATTEMPTS,
    AgentTrace,
    DraftFailure,
    DraftingStep,
    EvidenceReference,
    ExtractionStep,
    RetrievalStep,
    RulesStep,
    ScoringStep,
    StepStatus,
)
from src.db.models import EndState
from src.db.session import session_scope
from src.drafting.citation_validator import CitationProblemKind
from src.drafting.drafter import (
    DraftBlockedError,
    DraftFactError,
    DraftingError,
    DraftParseError,
    DraftPlaceholderError,
    DraftResult,
    DraftTruncatedError,
    draft_appeal,
)
from src.drafting.prompts import DraftingPrompt
from src.drafting.search_question import (
    MIN_EVIDENCE_SIMILARITY,
    SEARCH_QUESTION_VERSION,
    build_search_question,
)
from src.drafting.store import DraftCandidate
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


class LoopInput(Protocol):
    """What the loop reads about one denied letter.

    `text_sha256` is the hash of the letter text, `letter` its extracted fields and
    `extraction_prompt_version` the version they were read with. `features` are the model
    inputs, or None when they could not be built; the loop then stops before the score.
    """

    @property
    def text_sha256(self) -> str: ...

    @property
    def letter(self) -> DenialLetterExtraction: ...

    @property
    def extraction_prompt_version(self) -> str: ...

    @property
    def features(self) -> ClaimFeatures | None: ...


class AgentOutcome(BaseModel):
    """What the loop did with one letter, and the checked letter when it drafted one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trace: AgentTrace
    # Left out of the repr: letter text must never reach a log.
    draft: DraftResult | None = Field(default=None, repr=False)


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
    candidate: LoopInput,
    *,
    today: date,
    amount_floor: Decimal,
    win_threshold: float,
    embedding_model: str,
) -> AgentOutcome:
    """Run the loop for `candidate` and return its trace, with the checked letter if any.

    Spends money and can take several seconds when the claim reaches the last two steps: one
    embedding call and one or two model calls through `gateway`. A claim the rules or the threshold
    stop costs nothing. `embedding_model` must be the model the gateway embeds with.

    A chunk less similar to the search question than `MIN_EVIDENCE_SIMILARITY` is not shown
    to the drafting model. A claim with no chunk left is still drafted, with no evidence.

    A claim without `features` stops before the score as `needs_review_no_draft`, like a
    `must_review` verdict, with no model call. Nothing is stored: `session_factory` is only
    read by the evidence search. A draft that is blocked or not parseable is drafted once
    more with the same chunks. A draft that is cut off, holds placeholder text or states a
    wrong fact, or a second draft that fails, ends as `draft_failed`, with no letter.

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
        return AgentOutcome(
            trace=AgentTrace(end_state=end_state, extraction=extraction, rules=rules)
        )

    if candidate.features is None:
        # A value the model needs is missing from the letter: a person decides instead.
        return AgentOutcome(
            trace=AgentTrace(
                end_state=EndState.NEEDS_REVIEW_NO_DRAFT,
                extraction=extraction,
                rules=rules,
                scoring=ScoringStep(status=StepStatus.STOPPED, threshold=win_threshold),
            )
        )
    score = predict_win_probability(model, candidate.features)
    scoring = ScoringStep(
        status=StepStatus.DONE if score >= win_threshold else StepStatus.STOPPED,
        feature_version=FEATURE_VERSION,
        model_version=MODEL_VERSION,
        score=score,
        threshold=win_threshold,
    )
    if scoring.status is StepStatus.STOPPED:
        return AgentOutcome(
            trace=AgentTrace(
                end_state=EndState.BELOW_THRESHOLD,
                extraction=extraction,
                rules=rules,
                scoring=scoring,
            )
        )

    question = build_search_question(letter.denial_reason_category.value)
    with session_scope(session_factory) as session:
        matches = search_evidence(
            session,
            gateway,
            question,
            model_name=embedding_model,
            min_similarity=MIN_EVIDENCE_SIMILARITY,
        )
    retrieval = RetrievalStep(
        status=StepStatus.DONE,
        search_question_version=SEARCH_QUESTION_VERSION,
        embedding_model=embedding_model,
        min_similarity=MIN_EVIDENCE_SIMILARITY,
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

    attempts = 0
    while True:
        attempts += 1
        try:
            result = draft_appeal(gateway, prompt, letter, matches)
            break
        except (DraftBlockedError, DraftParseError) as error:
            # The model answers differently each time; a second try often gives a clean draft.
            if attempts < MAX_DRAFT_ATTEMPTS:
                continue
            failed_error: DraftingError = error
        except DraftingError as error:
            failed_error = error
        return AgentOutcome(
            trace=AgentTrace(
                end_state=EndState.DRAFT_FAILED,
                extraction=extraction,
                rules=rules,
                scoring=scoring,
                retrieval=retrieval,
                drafting=_failed_drafting_step(prompt, failed_error, attempts),
            )
        )
    return AgentOutcome(
        trace=AgentTrace(
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
                attempts=attempts,
            ),
        ),
        draft=result,
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


def _failed_drafting_step(
    prompt: DraftingPrompt, error: DraftingError, attempts: int
) -> DraftingStep:
    problem_kinds: tuple[CitationProblemKind, ...] = ()
    if isinstance(error, DraftBlockedError):
        failure = DraftFailure.BLOCKED
        found = {problem.kind for problem in error.problems}
        problem_kinds = tuple(kind for kind in CitationProblemKind if kind in found)
    elif isinstance(error, DraftTruncatedError):
        failure = DraftFailure.CUT_OFF
    elif isinstance(error, DraftPlaceholderError):
        failure = DraftFailure.PLACEHOLDER_TEXT
    elif isinstance(error, DraftFactError):
        failure = DraftFailure.FACT_CHECK
    else:
        failure = DraftFailure.NOT_PARSEABLE
    return DraftingStep(
        status=StepStatus.FAILED,
        prompt_version=prompt.version,
        failure=failure,
        problem_kinds=problem_kinds,
        attempts=attempts,
    )
