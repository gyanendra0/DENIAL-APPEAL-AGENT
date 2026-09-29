"""core tables: accounts, users, claims, denials

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_ROLE = ("viewer", "reviewer", "admin")
DENIAL_STATUS = ("new", "in_review", "appealed", "closed")


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _account_fk() -> sa.Column[Any]:
    return sa.Column(
        "account_id",
        sa.Integer(),
        sa.ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        *_timestamps(),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.Enum(*USER_ROLE, name="user_role"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("account_id", "email", name="uq_users_account_email"),
    )
    op.create_index("ix_users_account_id", "users", ["account_id"])

    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("claim_number", sa.String(64), nullable=False),
        sa.Column("payer", sa.String(200), nullable=False),
        sa.Column("billed_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("account_id", "claim_number", name="uq_claims_account_number"),
    )
    op.create_index("ix_claims_account_id", "claims", ["account_id"])

    op.create_table(
        "denials",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(20), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("denied_on", sa.Date(), nullable=False),
        sa.Column("status", sa.Enum(*DENIAL_STATUS, name="denial_status"), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_denials_account_id", "denials", ["account_id"])
    op.create_index("ix_denials_claim_id", "denials", ["claim_id"])


def downgrade() -> None:
    op.drop_table("denials")
    op.drop_table("claims")
    op.drop_table("users")
    op.drop_table("accounts")
    bind = op.get_bind()
    sa.Enum(name="denial_status").drop(bind, checkfirst=True)
    sa.Enum(name="user_role").drop(bind, checkfirst=True)
    op.execute("DROP EXTENSION IF EXISTS vector")
