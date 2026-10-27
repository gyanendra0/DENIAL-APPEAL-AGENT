"""email_codes (emailed 6-digit codes), mail_sends (the daily send cap), users.email_verified_at
and audit_action email_verified

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-27
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODE_TABLE = "email_codes"
SEND_TABLE = "mail_sends"
NEW_ENUMS = ("email_code_purpose",)
EMAIL_CODE_PURPOSE = ("verify_email", "password_reset")
NEW_AUDIT_ACTION = "email_verified"
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
)
OLD_TYPE_NAME = "audit_action_old"
JOB_CHECK_NAME = "ck_audit_events_job_matches_action"
# `::text`, because Postgres refuses to use a new enum value in the transaction that added it.
JOB_MATCHES_ACTION = (
    "(action::text IN ('account_created', 'password_changed', 'password_reset',"
    " 'google_linked', 'email_verified')) = (job_id IS NULL)"
)
JOB_MATCHES_ACTION_BEFORE = (
    "(action::text IN"
    " ('account_created', 'password_changed', 'password_reset', 'google_linked'))"
    " = (job_id IS NULL)"
)
CODE_HASH_FORMAT = "code_hash ~ '^[0-9a-f]{64}$'"
WRONG_TRIES_NOT_NEGATIVE = "wrong_tries >= 0"


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

    # Every user made before codes existed counts as verified from the day they were made.
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE users SET email_verified_at = created_at")

    purpose = postgresql.ENUM(*EMAIL_CODE_PURPOSE, name="email_code_purpose")
    purpose.create(op.get_bind())
    # Made once above; both tables use it.
    purpose_column = postgresql.ENUM(name="email_code_purpose", create_type=False)

    op.create_table(
        CODE_TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", purpose_column, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("wrong_tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        # Cascade: a code means nothing once its user is gone.
        sa.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["users.account_id", "users.id"],
            name="fk_email_codes_user",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(CODE_HASH_FORMAT, name="ck_email_codes_code_hash_format"),
        sa.CheckConstraint(
            WRONG_TRIES_NOT_NEGATIVE, name="ck_email_codes_wrong_tries_not_negative"
        ),
    )
    op.create_index("ix_email_codes_account_id", CODE_TABLE, ["account_id"])
    op.create_index(
        "ix_email_codes_account_id_user_id_purpose_created_at",
        CODE_TABLE,
        ["account_id", "user_id", "purpose", "created_at"],
    )

    op.create_table(
        SEND_TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purpose", purpose_column, nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_mail_sends_created_at", SEND_TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mail_sends_created_at", table_name=SEND_TABLE)
    op.drop_table(SEND_TABLE)
    op.drop_index("ix_email_codes_account_id_user_id_purpose_created_at", table_name=CODE_TABLE)
    op.drop_index("ix_email_codes_account_id", table_name=CODE_TABLE)
    op.drop_table(CODE_TABLE)
    for enum_name in NEW_ENUMS:
        sa.Enum(name=enum_name).drop(op.get_bind())

    op.drop_column("users", "email_verified_at")

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
