"""claim_samples and claim_sample_lines: public reference tables of synthetic claims

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AMOUNT_COLUMNS = (
    "payment_amount",
    "deductible_amount",
    "primary_payer_paid_amount",
    "coinsurance_amount",
    "allowed_charge_amount",
)
MAX_LINE_NUMBER = 13


def upgrade() -> None:
    op.create_table(
        "claim_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_claim_id", sa.String(15), nullable=False),
        sa.Column("claim_from_date", sa.Date(), nullable=False),
        sa.Column("claim_thru_date", sa.Date(), nullable=False),
        sa.Column("diagnosis_codes", postgresql.ARRAY(sa.String(5)), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("source_claim_id", name="uq_claim_samples_source_claim_id"),
        sa.CheckConstraint(
            "source_claim_id ~ '^[0-9]{15}$'", name="ck_claim_samples_source_claim_id_format"
        ),
        sa.CheckConstraint(
            "claim_from_date <= claim_thru_date", name="ck_claim_samples_from_not_after_thru"
        ),
    )
    op.create_table(
        "claim_sample_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_claim_id", sa.String(15), nullable=False),
        sa.Column("line_number", sa.SmallInteger(), nullable=False),
        sa.Column("hcpcs_code", sa.String(5), nullable=True),
        sa.Column("line_diagnosis_code", sa.String(5), nullable=True),
        sa.Column("processing_indicator", sa.String(1), nullable=False),
        *(sa.Column(name, sa.Numeric(12, 2), nullable=False) for name in AMOUNT_COLUMNS),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "source_claim_id", "line_number", name="uq_claim_sample_lines_claim_line"
        ),
        sa.ForeignKeyConstraint(
            ["source_claim_id"],
            ["claim_samples.source_claim_id"],
            name="fk_claim_sample_lines_claim",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"line_number BETWEEN 1 AND {MAX_LINE_NUMBER}",
            name="ck_claim_sample_lines_line_number_in_range",
        ),
        sa.CheckConstraint(
            "char_length(processing_indicator) = 1",
            name="ck_claim_sample_lines_processing_indicator_one_char",
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in AMOUNT_COLUMNS),
            name="ck_claim_sample_lines_amounts_non_negative",
        ),
    )


def downgrade() -> None:
    op.drop_table("claim_sample_lines")
    op.drop_table("claim_samples")
