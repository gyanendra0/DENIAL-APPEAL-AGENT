"""password_reset_tokens (one row per emailed reset link) and audit_action password_reset

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "password_reset_tokens"
NEW_AUDIT_ACTION = "password_reset"
# The values the type had before this migration; the downgrade builds the type from them.
AUDIT_ACTION_BEFORE = (
    "account_created",
    "upload_created",
    "draft_edited",
    "draft_approved",
    "draft_rejected",
    "password_changed",
)
OLD_TYPE_NAME = "audit_action_old"
JOB_CHECK_NAME = "ck_audit_events_job_matches_action"
# `::text`, because Postgres refuses to use a new enum value in the transaction that added it.
JOB_MATCHES_ACTION = (
    "(action::text IN ('account_created', 'password_changed', 'password_reset'))"
    " = (job_id IS NULL)"
)
JOB_MATCHES_ACTION_BEFORE = (
    "(action::text IN ('account_created', 'password_changed')) = (job_id IS NULL)"
)
TOKEN_HASH_FORMAT = "token_hash ~ '^[0-9a-f]{64}$'"  # noqa: S105  (a check rule, not a token)


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.execute(f"ALTER TYPE audit_action ADD VALUE '{NEW_AUDIT_ACTION}'")
    op.drop_constraint(JOB_CHECK_NAME, "audit_events", type_="check")
    op.create_check_constraint(JOB_CHECK_NAME, "audit_events", JOB_MATCHES_ACTION)

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
        # Cascade: a reset link means nothing once its user is gone.
        sa.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["users.account_id", "users.id"],
            name="fk_password_reset_tokens_user",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(TOKEN_HASH_FORMAT, name="ck_password_reset_tokens_token_hash_format"),
    )
    op.create_index("ix_password_reset_tokens_account_id", TABLE, ["account_id"])
    # The per-email cap counts a user's rows of the last hour by `created_at`.
    op.create_index(
        "ix_password_reset_tokens_account_id_user_id_created_at",
        TABLE,
        ["account_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_account_id_user_id_created_at", table_name=TABLE)
    op.drop_index("ix_password_reset_tokens_account_id", table_name=TABLE)
    op.drop_table(TABLE)
    # Postgres cannot remove one enum value, so the type is built again without it. The rows
    # of the removed action go first; the old check would refuse them anyway (no job).
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action::text = :action").bindparams(
            action=NEW_AUDIT_ACTION
        )
    )
    op.drop_constraint(JOB_CHECK_NAME, "audit_events", type_="check")
    op.execute(f"ALTER TYPE audit_action RENAME TO {OLD_TYPE_NAME}")
    sa.Enum(*AUDIT_ACTION_BEFORE, name="audit_action").create(op.get_bind())
    op.execute(
        "ALTER TABLE audit_events ALTER COLUMN action TYPE audit_action"
        " USING action::text::audit_action"
    )
    op.execute(f"DROP TYPE {OLD_TYPE_NAME}")
    op.create_check_constraint(JOB_CHECK_NAME, "audit_events", JOB_MATCHES_ACTION_BEFORE)
