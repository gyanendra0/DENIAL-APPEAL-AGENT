"""document_extractions: the fields a model read from one generated document

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROMPT_VERSION_NOT_EMPTY = "prompt_version <> ''"
MODEL_NAME_NOT_EMPTY = "model_name <> ''"
TEXT_SHA256_FORMAT = "text_sha256 ~ '^[0-9a-f]{64}$'"


def upgrade() -> None:
    # Both enum types already exist (0006 and 0008), so they are used, not created.
    op.create_table(
        "document_extractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_claim_id", sa.String(15), nullable=False),
        sa.Column(
            "document_type",
            postgresql.ENUM(name="document_type", create_type=False),
            nullable=False,
        ),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column(
            "provider", postgresql.ENUM(name="llm_provider", create_type=False), nullable=False
        ),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["source_claim_id"],
            ["claim_samples.source_claim_id"],
            name="fk_document_extractions_claim",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "source_claim_id",
            "document_type",
            "prompt_version",
            name="uq_document_extractions_claim_type_prompt",
        ),
        sa.CheckConstraint(
            PROMPT_VERSION_NOT_EMPTY, name="ck_document_extractions_prompt_version_not_empty"
        ),
        sa.CheckConstraint(
            MODEL_NAME_NOT_EMPTY, name="ck_document_extractions_model_name_not_empty"
        ),
        sa.CheckConstraint(TEXT_SHA256_FORMAT, name="ck_document_extractions_text_sha256_format"),
    )


def downgrade() -> None:
    # The enum types stay: `generated_documents` and `llm_calls` still use them.
    op.drop_table("document_extractions")
