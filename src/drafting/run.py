"""Drafts an appeal for a list of claims one at a time and saves each checked letter as it arrives.

Per claim: build the search question, search the evidence (a small paid embedding call), draft
the letter (a paid model call, which also runs the citation validator), store the letter. A
claim that already has a draft of the prompt version is skipped, so a run that stopped half
way is continued, not paid for twice. A draft the validator blocks is counted and not stored.
"""

import logging
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import LlmProvider
from src.db.session import session_scope
from src.drafting.drafter import (
    DraftBlockedError,
    DraftingError,
    DraftTruncatedError,
    draft_appeal,
)
from src.drafting.prompts import DraftingPrompt
from src.drafting.search_question import build_search_question
from src.drafting.store import (
    DraftCandidate,
    build_appeal_draft_row,
    read_drafted_claim_ids,
    save_appeal_draft,
)
from src.llm.gateway import (
    EmbeddingAnswerError,
    LlmBudgetExceededError,
    LlmGateway,
    LlmUnavailableError,
    ProviderRejectedError,
)
from src.rag.retrieval import EvidenceNotEmbeddedError, QueryEmbeddingError, search_evidence

logger = logging.getLogger(__name__)

# A progress line is logged each time this many claims have been looked at.
PROGRESS_EVERY = 25
BUDGET_REACHED = "the monthly LLM budget is reached"
NO_PROVIDER = "no provider could answer"
REQUEST_REFUSED = "a provider refused the request"
EVIDENCE_NOT_EMBEDDED = "the evidence cannot be searched"
SEARCH_ANSWER_NOT_USABLE = "the answer to a search question's embedding call cannot be used"


class DraftingRunSummary(BaseModel):
    """What one run did with the claims it was given.

    `blocked` counts the drafts the citation validator blocked; `blocked_by_kind` says, per
    kind of problem, how many of those drafts had it (a draft with two kinds counts under
    both). `stop_reason` is None when every claim was looked at; otherwise it says why the
    run ended early, and `not_tried` claims are left for the next run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected: int = Field(ge=0)
    already_drafted: int = Field(ge=0)
    drafted: int = Field(ge=0)
    drafted_with_citation: int = Field(ge=0)
    blocked: int = Field(ge=0)
    blocked_by_kind: dict[str, int]
    failed_to_parse: int = Field(ge=0)
    stopped_for_length: int = Field(ge=0)
    answered_by_fallback: int = Field(ge=0)
    stop_reason: str | None

    @property
    def not_tried(self) -> int:
        return (
            self.selected
            - self.already_drafted
            - self.drafted
            - self.blocked
            - self.failed_to_parse
            - self.stopped_for_length
        )


def run_drafting(
    session_factory: sessionmaker[Session],
    gateway: LlmGateway,
    prompt: DraftingPrompt,
    candidates: Sequence[DraftCandidate],
    *,
    embedding_model: str,
) -> DraftingRunSummary:
    """Draft an appeal for each of `candidates` that has no draft of `prompt`'s version.

    Spends money and can take minutes: one embedding call and one model call per claim,
    several seconds each. `embedding_model` must be the model the gateway embeds with. Each
    letter is committed on its own, so a run that stops keeps what it finished. A draft that
    is blocked, cut off or not parseable is counted and nothing is stored for it. The run
    ends early, without an error, when the budget is reached, no provider can answer, a
    provider refuses a request, the evidence has no stored vector, or the answer to a search
    question's embedding call cannot be used.
    """
    with session_scope(session_factory) as session:
        drafted_before = read_drafted_claim_ids(session, prompt.version)

    already_drafted = drafted = drafted_with_citation = 0
    blocked = failed_to_parse = stopped_for_length = answered_by_fallback = 0
    blocked_by_kind: Counter[str] = Counter()
    stop_reason: str | None = None
    for position, candidate in enumerate(candidates, start=1):
        if candidate.source_claim_id in drafted_before:
            already_drafted += 1
        else:
            letter = candidate.letter
            question = build_search_question(
                letter.denial_reason_category.value,
                [
                    line.hcpcs_code
                    for line in letter.denied_lines.value or ()
                    if line.hcpcs_code is not None
                ],
            )
            try:
                with session_scope(session_factory) as session:
                    matches = search_evidence(
                        session, gateway, question, model_name=embedding_model
                    )
                result = draft_appeal(gateway, prompt, letter, matches)
            except DraftTruncatedError:
                stopped_for_length += 1
            except DraftBlockedError as error:
                blocked += 1
                blocked_by_kind.update({problem.kind.value for problem in error.problems})
            except DraftingError:
                failed_to_parse += 1
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
            else:
                with session_scope(session_factory) as session:
                    save_appeal_draft(session, build_appeal_draft_row(candidate, result))
                drafted += 1
                drafted_with_citation += any(
                    paragraph.citations for paragraph in result.letter.paragraphs
                )
                answered_by_fallback += result.provider is LlmProvider.FALLBACK
        if position % PROGRESS_EVERY == 0:
            logger.info(
                "drafting progress: %d of %d claims (%d drafted, %d blocked, %d not usable)",
                position,
                len(candidates),
                drafted,
                blocked,
                failed_to_parse + stopped_for_length,
            )
    return DraftingRunSummary(
        selected=len(candidates),
        already_drafted=already_drafted,
        drafted=drafted,
        drafted_with_citation=drafted_with_citation,
        blocked=blocked,
        blocked_by_kind=dict(blocked_by_kind),
        failed_to_parse=failed_to_parse,
        stopped_for_length=stopped_for_length,
        answered_by_fallback=answered_by_fallback,
        stop_reason=stop_reason,
    )
