"""agent_runs: the end state and the step-by-step trace of one agent loop run per claim

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENT_END_STATE = (
    "needs_review",
    "needs_review_no_draft",
    "stopped_by_rules",
    "below_threshold",
    "draft_failed",
)
AGENT_VERSION_NOT_EMPTY = "agent_version <> ''"
TEXT_SHA256_FORMAT = "text_sha256 ~ '^[0-9a-f]{64}$'"
# IS NOT DISTINCT FROM, so a trace without the key is refused too (`=` would give NULL).
TRACE_END_STATE_MATCHES = "(trace ->> 'end_state') IS NOT DISTINCT FROM end_state::text"
TRACE_AGENT_VERSION_MATCHES = "(trace ->> 'agent_version') IS NOT DISTINCT FROM agent_version"


def upgrade() -> None:
    # No link to `appeal_drafts` or the evidence tables: the trace names them by value.
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_claim_id", sa.String(15), nullable=False),
        sa.Column("agent_version", sa.String(40), nullable=False),
        sa.Column("end_state", sa.Enum(*AGENT_END_STATE, name="agent_end_state"), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("trace", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["source_claim_id"],
            ["claim_samples.source_claim_id"],
            name="fk_agent_runs_claim",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "source_claim_id", "agent_version", name="uq_agent_runs_claim_agent_version"
        ),
        sa.CheckConstraint(AGENT_VERSION_NOT_EMPTY, name="ck_agent_runs_agent_version_not_empty"),
        sa.CheckConstraint(TEXT_SHA256_FORMAT, name="ck_agent_runs_text_sha256_format"),
        sa.CheckConstraint(TRACE_END_STATE_MATCHES, name="ck_agent_runs_trace_end_state_matches"),
        sa.CheckConstraint(
            TRACE_AGENT_VERSION_MATCHES, name="ck_agent_runs_trace_agent_version_matches"
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
    sa.Enum(name="agent_end_state").drop(op.get_bind(), checkfirst=True)
