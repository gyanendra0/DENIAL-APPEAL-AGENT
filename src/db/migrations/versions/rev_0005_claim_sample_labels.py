"""claim_sample_labels: public reference table of proxy labels and dataset splits

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DENIAL_REASON_CATEGORY = (
    "noncovered",
    "medical_necessity",
    "duplicate",
    "benefits_exhausted",
    "coordination_of_benefits",
    "other",
)
DATASET_SPLIT = ("train", "validation", "test")


def upgrade() -> None:
    op.create_table(
        "claim_sample_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_claim_id", sa.String(15), nullable=False),
        sa.Column("is_denied", sa.Boolean(), nullable=False),
        sa.Column(
            "denial_reason_category",
            sa.Enum(*DENIAL_REASON_CATEGORY, name="denial_reason_category"),
            nullable=True,
        ),
        sa.Column("appeal_success_proxy", sa.Boolean(), nullable=True),
        sa.Column("label_rule_version", sa.String(20), nullable=False),
        sa.Column("split", sa.Enum(*DATASET_SPLIT, name="dataset_split"), nullable=False),
        sa.Column("split_seed", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("source_claim_id", name="uq_claim_sample_labels_source_claim_id"),
        sa.ForeignKeyConstraint(
            ["source_claim_id"],
            ["claim_samples.source_claim_id"],
            name="fk_claim_sample_labels_claim",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "is_denied = (denial_reason_category IS NOT NULL)"
            " AND is_denied = (appeal_success_proxy IS NOT NULL)",
            name="ck_claim_sample_labels_denied_fields_match",
        ),
        sa.CheckConstraint(
            "label_rule_version <> ''", name="ck_claim_sample_labels_rule_version_not_empty"
        ),
    )


def downgrade() -> None:
    op.drop_table("claim_sample_labels")
    bind = op.get_bind()
    sa.Enum(name="dataset_split").drop(bind, checkfirst=True)
    sa.Enum(name="denial_reason_category").drop(bind, checkfirst=True)
