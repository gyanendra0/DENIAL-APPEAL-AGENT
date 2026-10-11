"""The typed record of what the agent loop did with one claim: five steps and an end state.

The steps are extraction, rules, scoring, retrieval and drafting, always in that order. Each
has a status and a few facts: versions, outcomes, a score, the fields that name a chunk. A
trace never holds document text, an extracted value, a search question or letter text.

Every end state is a stop for a person. None of them means "approved" or "sent". `EndState`
is defined in `src/db/models.py`, because the table `agent_runs` stores it as a column.

Any change to a step, to what stops the loop or to an end state needs a new `AGENT_VERSION`.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import EndState, EvidenceSource, LlmProvider
from src.drafting.citation_validator import CitationProblemKind
from src.rules.verdict import RuleOutcome, RuleReason

# `v2`: the evidence search drops a match below a minimum similarity.
AGENT_VERSION = "v2"


class StepStatus(StrEnum):
    DONE = "done"  # the step ran and the loop went on
    STOPPED = "stopped"  # the step ran and its answer ended the loop
    FAILED = "failed"  # the step could not give an answer for this claim
    NOT_RUN = "not_run"  # the loop ended before this step


class DraftFailure(StrEnum):
    """Why the drafting step failed."""

    BLOCKED = "blocked"  # the citation validator found a problem
    CUT_OFF = "cut_off"  # the answer reached the output limit
    NOT_PARSEABLE = "not_parseable"  # the answer is not JSON or does not fit a draft
    PLACEHOLDER_TEXT = "placeholder_text"  # a paragraph holds text in square brackets


class ExtractionStep(BaseModel):
    """The stored letter extraction the loop read. No extraction call is made."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: StepStatus
    prompt_version: str = Field(min_length=1)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RulesStep(BaseModel):
    """The rules' verdict and what they were run with.

    `outcome` is None when the step failed: the rules refuse an extracted amount below zero.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: StepStatus
    rules_version: str = Field(min_length=1)
    today: date
    amount_floor: Decimal
    outcome: RuleOutcome | None = None
    reasons: tuple[RuleReason, ...] = ()


class ScoringStep(BaseModel):
    """The model's score and the threshold it was compared with. All None when not run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: StepStatus
    feature_version: str | None = None
    model_version: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)
    threshold: float | None = Field(default=None, ge=0, le=1)


class EvidenceReference(BaseModel):
    """One chunk the search found: the four fields a citation uses, and how close it was."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EvidenceSource
    source_document_id: str
    chunk_index: int = Field(ge=0)
    text_sha256: str
    similarity: float


class RetrievalStep(BaseModel):
    """What the evidence search was run with and the chunks it found, the closest first.

    `min_similarity` is the lowest similarity a match could have. It is None when the step
    did not run and in a trace of agent version `v1`, whose search had no minimum. A step
    that is done with no match means no chunk reached the minimum.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: StepStatus
    search_question_version: str | None = None
    embedding_model: str | None = None
    min_similarity: float | None = None
    matches: tuple[EvidenceReference, ...] = ()


class DraftingStep(BaseModel):
    """Who drafted the letter, or why there is none.

    `cited_paragraphs` counts the paragraphs of a checked letter that carry a citation.
    `problem_kinds` lists, once each, the kinds of problem of a blocked draft.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: StepStatus
    prompt_version: str | None = None
    provider: LlmProvider | None = None
    model_name: str | None = None
    cited_paragraphs: int | None = Field(default=None, ge=0)
    failure: DraftFailure | None = None
    problem_kinds: tuple[CitationProblemKind, ...] = ()


class AgentTrace(BaseModel):
    """What the loop did with one claim, step by step, and where it stopped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    end_state: EndState
    extraction: ExtractionStep
    rules: RulesStep
    scoring: ScoringStep = ScoringStep(status=StepStatus.NOT_RUN)
    retrieval: RetrievalStep = RetrievalStep(status=StepStatus.NOT_RUN)
    drafting: DraftingStep = DraftingStep(status=StepStatus.NOT_RUN)
    agent_version: str = AGENT_VERSION
