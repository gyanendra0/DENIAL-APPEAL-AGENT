"""Runs the agent loop for a list of claims one at a time and stores each trace as it arrives.

A claim whose stored trace of this agent version ended in a final end state is skipped, so a
run that stopped half way is continued, not paid for twice. A claim whose draft failed is
taken again and its trace is replaced. Every stored trace is a stop for a person; nothing is
sent anywhere.
"""

import logging
from collections import Counter
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from src.agent.loop import AgentCandidate, run_agent_for_claim
from src.agent.store import build_agent_run_row, read_finished_claim_ids, save_agent_run
from src.agent.trace import AGENT_VERSION, StepStatus
from src.db.models import EndState, LlmProvider
from src.db.session import session_scope
from src.drafting.prompts import DraftingPrompt
from src.drafting.run import (
    BUDGET_REACHED,
    EVIDENCE_NOT_EMBEDDED,
    NO_PROVIDER,
    REQUEST_REFUSED,
    SEARCH_ANSWER_NOT_USABLE,
)
from src.llm.gateway import (
    EmbeddingAnswerError,
    LlmBudgetExceededError,
    LlmGateway,
    LlmUnavailableError,
    ProviderRejectedError,
)
from src.ml.training import WinModel
from src.rag.retrieval import EvidenceNotEmbeddedError, QueryEmbeddingError

logger = logging.getLogger(__name__)

# A progress line is logged each time this many claims have been looked at.
PROGRESS_EVERY = 25


class AgentRunSummary(BaseModel):
    """What one run did with the claims it was given.

    `end_states` counts the traces this run stored, by end state value. Of the `draft_failed`
    ones, `draft_failures` says how many drafts were blocked, cut off or not parseable, and
    `rules_failed` how many claims got no rules verdict. `stop_reason` is None when every
    claim was looked at; otherwise it says why the run ended early, and `not_tried` claims
    are left for the next run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected: int = Field(ge=0)
    already_finished: int = Field(ge=0)
    end_states: dict[str, int]
    drafted_with_citation: int = Field(ge=0)
    draft_failures: dict[str, int]
    rules_failed: int = Field(ge=0)
    answered_by_fallback: int = Field(ge=0)
    stop_reason: str | None

    @property
    def stored(self) -> int:
        return sum(self.end_states.values())

    @property
    def not_tried(self) -> int:
        return self.selected - self.already_finished - self.stored


def run_agent(
    session_factory: sessionmaker[Session],
    gateway: LlmGateway,
    prompt: DraftingPrompt,
    model: WinModel,
    candidates: Sequence[AgentCandidate],
    *,
    today: date,
    amount_floor: Decimal,
    win_threshold: float,
    embedding_model: str,
) -> AgentRunSummary:
    """Run the loop for each of `candidates` that has no final trace of this agent version.

    Spends money and can take minutes: one embedding call and one model call, several seconds
    each, per claim that passes the rules and the threshold. A claim they stop costs nothing.
    `embedding_model` must be the model the gateway embeds with. Each trace is committed on
    its own, so a run that stops keeps what it finished.

    The run ends early, without an error, when the budget is reached, no provider can answer,
    a provider refuses a request, the evidence has no stored vector, or the answer to a
    search question's embedding call cannot be used. No trace is stored for the claim it
    stopped at. Raises `ValueError` when `win_threshold` is not between 0 and 1.
    """
    with session_scope(session_factory) as session:
        finished_before = read_finished_claim_ids(session, AGENT_VERSION)

    already_finished = drafted_with_citation = rules_failed = answered_by_fallback = 0
    end_states: Counter[str] = Counter()
    draft_failures: Counter[str] = Counter()
    stop_reason: str | None = None
    for position, candidate in enumerate(candidates, start=1):
        if candidate.source_claim_id in finished_before:
            already_finished += 1
        else:
            try:
                trace = run_agent_for_claim(
                    session_factory,
                    gateway,
                    prompt,
                    model,
                    candidate,
                    today=today,
                    amount_floor=amount_floor,
                    win_threshold=win_threshold,
                    embedding_model=embedding_model,
                )
            except LlmBudgetExceededError:
                stop_reason = BUDGET_REACHED
                break
            except LlmUnavailableError:
                stop_reason = NO_PROVIDER
                break
            except ProviderRejectedError as error:
                # A bad request or a wrong key would most likely repeat on every claim.
                # The error's text is the kind of failure and the status only.
                stop_reason = f"{REQUEST_REFUSED}: {error}"
                break
            except EvidenceNotEmbeddedError as error:
                stop_reason = f"{EVIDENCE_NOT_EMBEDDED}: {error}"
                break
            except (QueryEmbeddingError, EmbeddingAnswerError) as error:
                # A vector of the wrong model or size would repeat on every claim, each time
                # paid for. The error's text holds no number of the vector.
                stop_reason = f"{SEARCH_ANSWER_NOT_USABLE}: {error}"
                break
            with session_scope(session_factory) as session:
                save_agent_run(session, build_agent_run_row(candidate, trace))
            end_states[trace.end_state.value] += 1
            drafted_with_citation += bool(trace.drafting.cited_paragraphs)
            answered_by_fallback += trace.drafting.provider is LlmProvider.FALLBACK
            rules_failed += trace.rules.status is StepStatus.FAILED
            if trace.drafting.failure is not None:
                draft_failures[trace.drafting.failure.value] += 1
        if position % PROGRESS_EVERY == 0:
            logger.info(
                "agent progress: %d of %d claims (%d with a draft, %d stopped before a"
                " draft, %d failed)",
                position,
                len(candidates),
                end_states[EndState.NEEDS_REVIEW.value],
                end_states[EndState.NEEDS_REVIEW_NO_DRAFT.value]
                + end_states[EndState.STOPPED_BY_RULES.value]
                + end_states[EndState.BELOW_THRESHOLD.value],
                end_states[EndState.DRAFT_FAILED.value],
            )
    return AgentRunSummary(
        selected=len(candidates),
        already_finished=already_finished,
        end_states={state.value: end_states[state.value] for state in EndState},
        drafted_with_citation=drafted_with_citation,
        draft_failures=dict(draft_failures),
        rules_failed=rules_failed,
        answered_by_fallback=answered_by_fallback,
        stop_reason=stop_reason,
    )
