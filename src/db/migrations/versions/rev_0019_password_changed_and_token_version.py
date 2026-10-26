"""audit_action password_changed (no job) and users.token_version, to end other sessions

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_AUDIT_ACTION = "password_changed"
# The values the type had before this migration; the downgrade builds the type from them.
AUDIT_ACTION_BEFORE = (
    "account_created",
    "upload_created",
    "draft_edited",
    "draft_approved",
    "draft_rejected",
)
OLD_TYPE_NAME = "audit_action_old"
JOB_CHECK_NAME = "ck_audit_events_job_matches_action"
# `::text`, because Postgres refuses to use a new enum value in the transaction that added it.
JOB_MATCHES_ACTION = "(action::text IN ('account_created', 'password_changed')) = (job_id IS NULL)"
JOB_MATCHES_ACTION_BEFORE = "(action = 'account_created') = (job_id IS NULL)"
TOKEN_VERSION_NOT_NEGATIVE = "token_version >= 0"  # noqa: S105  (a check rule, not a token)


def upgrade() -> None:
    op.execute(f"ALTER TYPE audit_action ADD VALUE '{NEW_AUDIT_ACTION}'")
    op.drop_constraint(JOB_CHECK_NAME, "audit_events", type_="check")
    op.create_check_constraint(JOB_CHECK_NAME, "audit_events", JOB_MATCHES_ACTION)
    # Existing users start at 0. Tokens issued before this change carry no version and are
    # refused (`ver` is a required claim), so each user logs in once more.
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_users_token_version_not_negative", "users", TOKEN_VERSION_NOT_NEGATIVE
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_token_version_not_negative", "users", type_="check")
    op.drop_column("users", "token_version")
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
