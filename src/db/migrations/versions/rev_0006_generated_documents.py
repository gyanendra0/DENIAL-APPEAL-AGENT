"""generated_documents: public reference table of fabricated documents for the claims sample

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENT_TYPE = ("denial_letter", "clinical_note", "prior_auth")
TEMPLATE_ID_NOT_EMPTY = "template_id <> ''"
SEED_NOT_NEGATIVE = "seed >= 0"
GENERATOR_VERSION_NOT_EMPTY = "generator_version <> ''"
TEXT_NOT_EMPTY = "text <> ''"


def upgrade() -> None:
    op.create_table(
        "generated_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_claim_id", sa.String(15), nullable=False),
        sa.Column("document_type", sa.Enum(*DOCUMENT_TYPE, name="document_type"), nullable=False),
        sa.Column("template_id", sa.String(40), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("generator_version", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("answer_key", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "source_claim_id", "document_type", name="uq_generated_documents_claim_type"
        ),
        sa.ForeignKeyConstraint(
            ["source_claim_id"],
            ["claim_samples.source_claim_id"],
            name="fk_generated_documents_claim",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            TEMPLATE_ID_NOT_EMPTY, name="ck_generated_documents_template_id_not_empty"
        ),
        sa.CheckConstraint(SEED_NOT_NEGATIVE, name="ck_generated_documents_seed_not_negative"),
        sa.CheckConstraint(
            GENERATOR_VERSION_NOT_EMPTY, name="ck_generated_documents_generator_version_not_empty"
        ),
        sa.CheckConstraint(TEXT_NOT_EMPTY, name="ck_generated_documents_text_not_empty"),
    )


def downgrade() -> None:
    op.drop_table("generated_documents")
    sa.Enum(name="document_type").drop(op.get_bind(), checkfirst=True)
