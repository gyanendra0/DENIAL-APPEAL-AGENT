"""login_failures: one row per failed login per key (email or IP), stored as a keyed hash

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOGIN_KEY_KIND = ("email", "ip")
KEY_HASH_FORMAT = "key_hash ~ '^[0-9a-f]{64}$'"

NEW_ENUMS = ("login_key_kind",)


def upgrade() -> None:
    op.create_table(
        "login_failures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key_kind", sa.Enum(*LOGIN_KEY_KIND, name="login_key_kind"), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(KEY_HASH_FORMAT, name="ck_login_failures_key_hash_format"),
    )
    op.create_index(
        "ix_login_failures_key_kind_key_hash_created_at",
        "login_failures",
        ["key_kind", "key_hash", "created_at"],
    )
    # Every try deletes the rows older than the window; without this it would scan the table.
    op.create_index("ix_login_failures_created_at", "login_failures", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_login_failures_created_at", table_name="login_failures")
    op.drop_index("ix_login_failures_key_kind_key_hash_created_at", table_name="login_failures")
    op.drop_table("login_failures")
    for enum_name in NEW_ENUMS:
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
