"""audit_action user_joined (no job) and index uploads (account_id, user_id) for the owner filter

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_AUDIT_ACTION = "user_joined"
# The values the type had before this migration; the downgrade builds the type from them.
AUDIT_ACTION_BEFORE = (
    "account_created",
    "upload_created",
    "draft_edited",
    "draft_approved",
    "draft_rejected",
    "password_changed",
    "password_reset",
    "google_linked",
    "email_verified",
)
OLD_TYPE_NAME = "audit_action_old"
JOB_CHECK_NAME = "ck_audit_events_job_matches_action"
# `::text`, because Postgres refuses to use a new enum value in the transaction that added it.
JOB_MATCHES_ACTION = (
    "(action::text IN ('account_created', 'password_changed', 'password_reset',"
    " 'google_linked', 'email_verified', 'user_joined')) = (job_id IS NULL)"
)
JOB_MATCHES_ACTION_BEFORE = (
    "(action::text IN ('account_created', 'password_changed', 'password_reset',"
    " 'google_linked', 'email_verified')) = (job_id IS NULL)"
)
OWNER_INDEX = "ix_uploads_account_id_user_id"


def upgrade() -> None:
    op.execute(f"ALTER TYPE audit_action ADD VALUE '{NEW_AUDIT_ACTION}'")
    op.drop_constraint(JOB_CHECK_NAME, "audit_events", type_="check")
    op.create_check_constraint(JOB_CHECK_NAME, "audit_events", JOB_MATCHES_ACTION)
    op.create_index(OWNER_INDEX, "uploads", ["account_id", "user_id"])


def downgrade() -> None:
    # Checked first, so a refused downgrade changes nothing. These rows are the only record of
    # who joined the shared account, so they are not deleted to make room.
    joined = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM audit_events WHERE action::text = :action").bindparams(
            action=NEW_AUDIT_ACTION
        )
    )
    if joined:
        # A plain RuntimeError: Alembic loads this file under its own module name, so a class
        # defined here would not be the one a caller imports.
        raise RuntimeError(
            f"{joined} audit row(s) record a user joining; downgrading below 0023 would lose them"
        )

    op.drop_index(OWNER_INDEX, table_name="uploads")
    # Postgres cannot remove one enum value, so the type is built again without it.
    op.drop_constraint(JOB_CHECK_NAME, "audit_events", type_="check")
    op.execute(f"ALTER TYPE audit_action RENAME TO {OLD_TYPE_NAME}")
    sa.Enum(*AUDIT_ACTION_BEFORE, name="audit_action").create(op.get_bind())
    op.execute(
        "ALTER TABLE audit_events ALTER COLUMN action TYPE audit_action"
        " USING action::text::audit_action"
    )
    op.execute(f"DROP TYPE {OLD_TYPE_NAME}")
    op.create_check_constraint(JOB_CHECK_NAME, "audit_events", JOB_MATCHES_ACTION_BEFORE)
