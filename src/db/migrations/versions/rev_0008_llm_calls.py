"""llm_calls: one row per answered model call, with its token counts and cost

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LLM_PROVIDER = ("primary", "fallback")
MODEL_NAME_NOT_EMPTY = "model_name <> ''"
PROMPT_VERSION_NOT_EMPTY = "prompt_version <> ''"
PURPOSE_NOT_EMPTY = "purpose <> ''"
INPUT_TOKENS_NOT_NEGATIVE = "input_tokens >= 0"
OUTPUT_TOKENS_NOT_NEGATIVE = "output_tokens >= 0"
COST_USD_NOT_NEGATIVE = "cost_usd >= 0"


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.Enum(*LLM_PROVIDER, name="llm_provider"), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(MODEL_NAME_NOT_EMPTY, name="ck_llm_calls_model_name_not_empty"),
        sa.CheckConstraint(PROMPT_VERSION_NOT_EMPTY, name="ck_llm_calls_prompt_version_not_empty"),
        sa.CheckConstraint(PURPOSE_NOT_EMPTY, name="ck_llm_calls_purpose_not_empty"),
        sa.CheckConstraint(
            INPUT_TOKENS_NOT_NEGATIVE, name="ck_llm_calls_input_tokens_not_negative"
        ),
        sa.CheckConstraint(
            OUTPUT_TOKENS_NOT_NEGATIVE, name="ck_llm_calls_output_tokens_not_negative"
        ),
        sa.CheckConstraint(COST_USD_NOT_NEGATIVE, name="ck_llm_calls_cost_usd_not_negative"),
    )
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_created_at", table_name="llm_calls")
    op.drop_table("llm_calls")
    sa.Enum(name="llm_provider").drop(op.get_bind(), checkfirst=True)
