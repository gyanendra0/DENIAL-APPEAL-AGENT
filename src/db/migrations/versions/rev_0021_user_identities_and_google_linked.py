"""user_identities (one Google account per user), users.password_hash nullable, and
audit_action google_linked

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-27
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "user_identities"
NEW_ENUMS = ("identity_provider",)
IDENTITY_PROVIDER = ("google",)
NEW_AUDIT_ACTION = "google_linked"
# The values the type had before this migration; the downgrade builds the type from them.
AUDIT_ACTION_BEFORE = (
    "account_created",
    "upload_created",
    "draft_edited",
    "draft_approved",
    "draft_rejected",
    "password_changed",
    "password_reset",
)
OLD_TYPE_NAME = "audit_action_old"
JOB_CHECK_NAME = "ck_audit_events_job_matches_action"
# `::text`, because Postgres refuses to use a new enum value in the transaction that added it.
JOB_MATCHES_ACTION = (
    "(action::text IN"
    " ('account_created', 'password_changed', 'password_reset', 'google_linked'))"
    " = (job_id IS NULL)"
)
JOB_MATCHES_ACTION_BEFORE = (
    "(action::text IN ('account_created', 'password_changed', 'password_reset'))"
    " = (job_id IS NULL)"
)
SUBJECT_NOT_EMPTY = "subject <> ''"
EMAIL_AT_LINK_NORMALIZED = "email_at_link = lower(email_at_link) AND email_at_link !~ '^\\s|\\s$'"


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

    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=True)

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
        sa.Column(
            "provider", sa.Enum(*IDENTITY_PROVIDER, name="identity_provider"), nullable=False
        ),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email_at_link", sa.String(320), nullable=False),
        *_timestamps(),
        # One Google account opens at most one user in the whole system.
        sa.UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
        sa.UniqueConstraint(
            "account_id",
            "user_id",
            "provider",
            name="uq_user_identities_account_id_user_id_provider",
        ),
        # Cascade: an identity means nothing once its user is gone.
        sa.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["users.account_id", "users.id"],
            name="fk_user_identities_user",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(SUBJECT_NOT_EMPTY, name="ck_user_identities_subject_not_empty"),
        sa.CheckConstraint(
            EMAIL_AT_LINK_NORMALIZED, name="ck_user_identities_email_at_link_normalized"
        ),
    )
    op.create_index("ix_user_identities_account_id", TABLE, ["account_id"])


def downgrade() -> None:
    # Checked first, so a refused downgrade changes nothing. Inventing a password would claim
    # one exists; the user can set one with "Forgot password" and the downgrade run again.
    without_password = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM users WHERE password_hash IS NULL")
    )
    if without_password:
        # A plain RuntimeError: Alembic loads this file under its own module name, so a class
        # defined here would not be the one a caller imports.
        raise RuntimeError(
            f"{without_password} user(s) have no password; each must set one"
            " (Forgot password) before downgrading below 0021"
        )

    op.drop_index("ix_user_identities_account_id", table_name=TABLE)
    op.drop_table(TABLE)
    for enum_name in NEW_ENUMS:
        sa.Enum(name=enum_name).drop(op.get_bind())

    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=False)

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
