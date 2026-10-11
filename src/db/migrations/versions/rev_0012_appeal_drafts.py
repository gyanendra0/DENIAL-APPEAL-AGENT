"""appeal_drafts: a drafted appeal letter that passed the citation validator

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROMPT_VERSION_NOT_EMPTY = "prompt_version <> ''"
SEARCH_QUESTION_VERSION_NOT_EMPTY = "search_question_version <> ''"
MODEL_NAME_NOT_EMPTY = "model_name <> ''"
TEXT_SHA256_FORMAT = "text_sha256 ~ '^[0-9a-f]{64}$'"


def upgrade() -> None:
    # The enum type `llm_provider` already exists (0008), so it is used, not created.
    # No link to the evidence tables: a citation names its chunk by four values in `letter`.
    op.create_table(
        "appeal_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_claim_id", sa.String(15), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("search_question_version", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column(
            "provider", postgresql.ENUM(name="llm_provider", create_type=False), nullable=False
        ),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("letter", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["source_claim_id"],
            ["claim_samples.source_claim_id"],
            name="fk_appeal_drafts_claim",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "source_claim_id", "prompt_version", name="uq_appeal_drafts_claim_prompt"
        ),
        sa.CheckConstraint(
            PROMPT_VERSION_NOT_EMPTY, name="ck_appeal_drafts_prompt_version_not_empty"
        ),
        sa.CheckConstraint(
            SEARCH_QUESTION_VERSION_NOT_EMPTY,
            name="ck_appeal_drafts_search_question_version_not_empty",
        ),
        sa.CheckConstraint(MODEL_NAME_NOT_EMPTY, name="ck_appeal_drafts_model_name_not_empty"),
        sa.CheckConstraint(TEXT_SHA256_FORMAT, name="ck_appeal_drafts_text_sha256_format"),
    )


def downgrade() -> None:
    # The enum type stays: `llm_calls` and `document_extractions` still use it.
    op.drop_table("appeal_drafts")
