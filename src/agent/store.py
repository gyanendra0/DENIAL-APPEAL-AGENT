"""Reads the claims the agent loop can take and stores each claim's trace in `agent_runs`.

`claim_sample_labels`, `claim_sample_lines` and `agent_runs` are public reference tables (no
`account_id`): they hold only the fabricated claims of the synthetic claims sample and what
was made from them.

A claim can be taken when it can be drafted (it is denied and its denial letter has a current
extraction) and its stored label still fits its stored lines, because the model inputs are
built from those rows. A trace belongs to a claim and an agent version. A claim whose trace
ended in a draft that failed is taken again; every other end state is final for that version.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.loop import AgentCandidate
from src.agent.trace import AgentTrace
from src.db.models import AgentRun, ClaimSampleLabel, ClaimSampleLine, DatasetSplit, EndState
from src.drafting.store import select_draft_candidates
from src.ingest.marketplace_denials import upsert_rows
from src.ml.features import build_claim_features
from src.ml.labels import ClaimId, ClaimLabel, label_claim

UPSERT_CONSTRAINT = "uq_agent_runs_claim_agent_version"
UPSERT_KEY_COLUMNS = ("source_claim_id", "agent_version")


class ClaimRowsError(Exception):
    """The stored label of a selected claim no longer fits its stored lines.

    Its text gives a count and names no claim.
    """


class AgentRunRow(BaseModel):
    """One claim's trace, shaped like the `agent_runs` table.

    `trace` is the `AgentTrace` as plain JSON values, because that is what the JSON column
    stores. The other three values are copied out of it and out of the candidate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    agent_version: str = Field(min_length=1, max_length=40)
    end_state: EndState
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trace: dict[str, Any] = Field(min_length=1)


def select_agent_candidates(
    session: Session,
    split: DatasetSplit,
    extraction_prompt_version: str,
    limit: int | None = None,
) -> list[AgentCandidate]:
    """Return the denied claims of `split` the loop can take, at most `limit`, by claim id.

    They are the claims `select_draft_candidates` returns, each with the model inputs built
    from its stored label and lines. Reads only; the same `limit` always picks the same
    claims.

    Raises `LetterExtractionError` when a stored extraction does not fit the letter schema,
    and `ClaimRowsError` when a selected claim has no lines or the label rule, applied to its
    stored lines, does not give exactly its stored label (the lines were loaded again after
    the labels).
    """
    drafts = select_draft_candidates(session, split, extraction_prompt_version, limit)
    claim_ids = [draft.source_claim_id for draft in drafts]
    labels = {
        label.source_claim_id: label
        for label in session.scalars(
            select(ClaimSampleLabel).where(ClaimSampleLabel.source_claim_id.in_(claim_ids))
        )
    }
    lines_by_claim: dict[str, list[ClaimSampleLine]] = {claim_id: [] for claim_id in claim_ids}
    for line in session.scalars(
        select(ClaimSampleLine).where(ClaimSampleLine.source_claim_id.in_(claim_ids))
    ):
        lines_by_claim[line.source_claim_id].append(line)

    candidates = []
    stale = 0
    for draft in drafts:
        stored = labels[draft.source_claim_id]
        lines = lines_by_claim[draft.source_claim_id]
        label = ClaimLabel(
            source_claim_id=stored.source_claim_id,
            is_denied=stored.is_denied,
            denial_reason_category=stored.denial_reason_category,
            appeal_success_proxy=stored.appeal_success_proxy,
            label_rule_version=stored.label_rule_version,
        )
        category = label.denial_reason_category
        if not lines or category is None or label_claim(draft.source_claim_id, lines) != label:
            stale += 1
            continue
        candidates.append(
            AgentCandidate(
                source_claim_id=draft.source_claim_id,
                text_sha256=draft.text_sha256,
                letter=draft.letter,
                extraction_prompt_version=extraction_prompt_version,
                features=build_claim_features(category, lines),
            )
        )
    if stale:
        raise ClaimRowsError(
            f"the stored label of {stale} of the {len(drafts)} selected claims no longer fits"
            " its stored lines: run pipelines.run_data_pipeline again"
        )
    return candidates


def read_finished_claim_ids(session: Session, agent_version: str) -> set[str]:
    """Return the claims whose trace of `agent_version` ended in a final end state.

    Every end state is final except `draft_failed`: such a claim is taken again.
    """
    return set(
        session.scalars(
            select(AgentRun.source_claim_id).where(
                AgentRun.agent_version == agent_version,
                AgentRun.end_state != EndState.DRAFT_FAILED,
            )
        )
    )


def build_agent_run_row(candidate: AgentCandidate, trace: AgentTrace) -> AgentRunRow:
    """Shape `trace`, what the loop did with `candidate`, for the table."""
    return AgentRunRow(
        source_claim_id=candidate.source_claim_id,
        agent_version=trace.agent_version,
        end_state=trace.end_state,
        text_sha256=trace.extraction.text_sha256,
        trace=trace.model_dump(mode="json"),
    )


def save_agent_run(session: Session, row: AgentRunRow) -> None:
    """Store `row`, replacing the trace its claim and agent version had.

    The claim must already be in `claim_samples`. Does not commit: the caller owns the
    transaction.
    """
    upsert_rows(session, AgentRun, [row], UPSERT_CONSTRAINT, UPSERT_KEY_COLUMNS)
