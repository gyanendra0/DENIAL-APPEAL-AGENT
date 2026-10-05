"""issuer_denial_stats: public reference table of issuer claim and appeal counts

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXCHANGE_TYPE = ("FFE", "SPE", "SBE-FP")
COUNT_COLUMNS = (
    "claims_received_out_of_network",
    "claims_received_in_network",
    "claims_denied_out_of_network",
    "claims_denied_in_network",
    "claims_resubmitted_out_of_network",
    "claims_resubmitted_in_network",
    "internal_appeals_filed",
    "internal_appeals_overturned",
    "external_appeals_filed",
    "external_appeals_overturned",
)
PERCENT_COLUMNS = ("internal_appeals_overturned_pct", "external_appeals_overturned_pct")


def upgrade() -> None:
    op.create_table(
        "issuer_denial_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_year", sa.Integer(), nullable=False),
        sa.Column("issuer_id", sa.String(5), nullable=False),
        sa.Column("issuer_name", sa.String(200), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("exchange_type", sa.Enum(*EXCHANGE_TYPE, name="exchange_type"), nullable=False),
        sa.Column("is_new_to_exchange", sa.Boolean(), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=True) for name in COUNT_COLUMNS),
        *(sa.Column(name, sa.Numeric(5, 2), nullable=True) for name in PERCENT_COLUMNS),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("issuer_id", "plan_year", name="uq_issuer_denial_stats_issuer_year"),
        sa.CheckConstraint(
            "issuer_id ~ '^[0-9]{5}$'", name="ck_issuer_denial_stats_issuer_id_format"
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in COUNT_COLUMNS),
            name="ck_issuer_denial_stats_counts_non_negative",
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} BETWEEN 0 AND 100" for name in PERCENT_COLUMNS),
            name="ck_issuer_denial_stats_percents_in_range",
        ),
    )


def downgrade() -> None:
    op.drop_table("issuer_denial_stats")
    sa.Enum(name="exchange_type").drop(op.get_bind(), checkfirst=True)
