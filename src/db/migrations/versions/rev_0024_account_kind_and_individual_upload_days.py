"""accounts.kind (shared or personal) and individual_upload_days (the daily cap of individuals)

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DAY_TABLE = "individual_upload_days"
NEW_ENUMS = ("account_kind",)
ACCOUNT_KIND = ("shared", "personal")
COUNT_NOT_NEGATIVE = "count >= 0"


def upgrade() -> None:
    kind = postgresql.ENUM(*ACCOUNT_KIND, name="account_kind")
    kind.create(op.get_bind())
    # Every account made before this revision is the team's, so it is `shared`.
    op.add_column(
        "accounts",
        sa.Column(
            "kind",
            postgresql.ENUM(name="account_kind", create_type=False),
            server_default="shared",
            nullable=False,
        ),
    )

    op.create_table(
        DAY_TABLE,
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(COUNT_NOT_NEGATIVE, name="ck_individual_upload_days_count_not_negative"),
    )


def downgrade() -> None:
    # Checked first, so a refused downgrade changes nothing. Without the column a personal
    # account would read as a team account, and its user would join the team's work.
    personal = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM accounts WHERE kind::text = 'personal'")
    )
    if personal:
        # A plain RuntimeError: Alembic loads this file under its own module name, so a class
        # defined here would not be the one a caller imports.
        raise RuntimeError(
            f"{personal} personal account(s) exist; downgrading below 0024 would make them shared"
        )

    op.drop_table(DAY_TABLE)
    op.drop_column("accounts", "kind")
    for enum_name in NEW_ENUMS:
        sa.Enum(name=enum_name).drop(op.get_bind())
