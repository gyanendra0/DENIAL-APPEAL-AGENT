"""evidence_chunk_embeddings: one vector per evidence chunk, model and input version

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 1536
MODEL_NAME_NOT_EMPTY = "model_name <> ''"
INPUT_VERSION_NOT_EMPTY = "input_version <> ''"


def upgrade() -> None:
    # The `vector` type comes from the extension that migration 0001 creates.
    op.create_table(
        "evidence_chunk_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evidence_chunk_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("input_version", sa.String(40), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["evidence_chunk_id"],
            ["evidence_chunks.id"],
            name="fk_evidence_chunk_embeddings_chunk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "evidence_chunk_id",
            "model_name",
            "input_version",
            name="uq_evidence_chunk_embeddings_chunk_model_version",
        ),
        sa.CheckConstraint(
            MODEL_NAME_NOT_EMPTY, name="ck_evidence_chunk_embeddings_model_name_not_empty"
        ),
        sa.CheckConstraint(
            INPUT_VERSION_NOT_EMPTY, name="ck_evidence_chunk_embeddings_input_version_not_empty"
        ),
    )


def downgrade() -> None:
    op.drop_table("evidence_chunk_embeddings")
