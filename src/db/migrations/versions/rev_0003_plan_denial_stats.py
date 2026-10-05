"""plan_denial_stats: public reference table of plan claim counts and denial reasons

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLAN_TYPE = ("HMO", "EPO", "PPO", "POS")
METAL_LEVEL = ("Bronze", "Silver", "Gold", "Platinum", "Catastrophic")
COUNT_COLUMNS = (
    "claims_received_out_of_network",
    "claims_received_in_network",
    "claims_denied_out_of_network",
    "claims_denied_in_network",
    "claims_resubmitted_out_of_network",
    "claims_resubmitted_in_network",
    "denied_referral_required",
    "denied_out_of_network",
    "denied_services_excluded",
    "denied_not_medically_necessary_non_bh",
    "denied_not_medically_necessary_bh",
    "denied_benefit_limit_reached",
    "denied_member_not_covered",
    "denied_investigational_experimental_cosmetic",
    "denied_administrative_reason",
    "denied_other",
)


def upgrade() -> None:
    op.create_table(
        "plan_denial_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_year", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.String(14), nullable=False),
        sa.Column("issuer_id", sa.String(5), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("plan_type", sa.Enum(*PLAN_TYPE, name="plan_type"), nullable=False),
        sa.Column("metal_level", sa.Enum(*METAL_LEVEL, name="metal_level"), nullable=False),
        sa.Column("is_reported", sa.Boolean(), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=True) for name in COUNT_COLUMNS),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("plan_id", "plan_year", name="uq_plan_denial_stats_plan_year"),
        sa.ForeignKeyConstraint(
            ["issuer_id", "plan_year"],
            ["issuer_denial_stats.issuer_id", "issuer_denial_stats.plan_year"],
            name="fk_plan_denial_stats_issuer_year",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "plan_id ~ '^[0-9]{5}[A-Z]{2}[0-9]{7}$'", name="ck_plan_denial_stats_plan_id_format"
        ),
        sa.CheckConstraint(
            "left(plan_id, 5) = issuer_id AND substr(plan_id, 6, 2) = state",
            name="ck_plan_denial_stats_plan_id_matches_issuer",
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in COUNT_COLUMNS),
            name="ck_plan_denial_stats_counts_non_negative",
        ),
        sa.CheckConstraint(
            "is_reported OR (" + " AND ".join(f"{name} IS NULL" for name in COUNT_COLUMNS) + ")",
            name="ck_plan_denial_stats_unreported_has_no_counts",
        ),
    )
    op.create_index(
        "ix_plan_denial_stats_issuer_year", "plan_denial_stats", ["issuer_id", "plan_year"]
    )


def downgrade() -> None:
    op.drop_index("ix_plan_denial_stats_issuer_year", table_name="plan_denial_stats")
    op.drop_table("plan_denial_stats")
    bind = op.get_bind()
    sa.Enum(name="metal_level").drop(bind, checkfirst=True)
    sa.Enum(name="plan_type").drop(bind, checkfirst=True)
