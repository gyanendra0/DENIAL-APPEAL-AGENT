"""draft review and audit: review columns on drafts, edited versions, audit events, job cost

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-16
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRAFT_REVIEW_STATUS = ("pending", "approved", "rejected")
DRAFT_REJECT_REASON = (
    "wrong_fact",
    "no_policy_argument",
    "citation_does_not_fit",
    "poor_wording",
    "other",
)
AUDIT_ACTION = (
    "account_created",
    "upload_created",
    "draft_edited",
    "draft_approved",
    "draft_rejected",
)

COST_USD_NOT_NEGATIVE = "cost_usd >= 0"
REVIEW_MATCHES_STATUS = (
    "(review_status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL"
    " AND reject_reason IS NULL AND approved_version IS NULL)"
    " OR (review_status = 'approved' AND reviewed_by IS NOT NULL"
    " AND reviewed_at IS NOT NULL AND reject_reason IS NULL"
    " AND approved_version IS NOT NULL)"
    " OR (review_status = 'rejected' AND reviewed_by IS NOT NULL"
    " AND reviewed_at IS NOT NULL AND reject_reason IS NOT NULL"
    " AND approved_version IS NULL)"
)
APPROVED_VERSION_NOT_NEGATIVE = "approved_version >= 0"
VERSION_POSITIVE = "version >= 1"
TEXT_SHA256_FORMAT = "text_sha256 ~ '^[0-9a-f]{64}$'"
JOB_MATCHES_ACTION = "(action = 'account_created') = (job_id IS NULL)"
DETAIL_IS_OBJECT = "jsonb_typeof(detail) = 'object'"

NEW_ENUMS = ("audit_action", "draft_reject_reason", "draft_review_status")


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
    # Every existing job gets cost 0: no job before this migration measured its cost.
    op.add_column(
        "jobs",
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
    )
    op.create_check_constraint("ck_jobs_cost_usd_not_negative", "jobs", COST_USD_NOT_NEGATIVE)

    review_status = postgresql.ENUM(*DRAFT_REVIEW_STATUS, name="draft_review_status")
    reject_reason = postgresql.ENUM(*DRAFT_REJECT_REASON, name="draft_reject_reason")
    review_status.create(op.get_bind())
    reject_reason.create(op.get_bind())

    # Every existing draft becomes `pending`: nobody has reviewed one yet.
    op.add_column(
        "upload_drafts",
        sa.Column(
            "review_status",
            postgresql.ENUM(name="draft_review_status", create_type=False),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column("upload_drafts", sa.Column("reviewed_by", sa.Integer(), nullable=True))
    op.add_column(
        "upload_drafts", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "upload_drafts",
        sa.Column(
            "reject_reason",
            postgresql.ENUM(name="draft_reject_reason", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("upload_drafts", sa.Column("approved_version", sa.Integer(), nullable=True))
    op.create_unique_constraint(
        "uq_upload_drafts_account_job_id", "upload_drafts", ["account_id", "job_id"]
    )
    # No cascade: a user who reviewed a draft cannot be deleted by accident.
    op.create_foreign_key(
        "fk_upload_drafts_reviewer",
        "upload_drafts",
        "users",
        ["account_id", "reviewed_by"],
        ["account_id", "id"],
    )
    op.create_check_constraint(
        "ck_upload_drafts_review_matches_status", "upload_drafts", REVIEW_MATCHES_STATUS
    )
    op.create_check_constraint(
        "ck_upload_drafts_approved_version_not_negative",
        "upload_drafts",
        APPROVED_VERSION_NOT_NEGATIVE,
    )

    op.create_table(
        "draft_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("letter", postgresql.JSONB(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("edited_by", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("job_id", "version", name="uq_draft_revisions_job_version"),
        sa.ForeignKeyConstraint(
            ["account_id", "job_id"],
            ["upload_drafts.account_id", "upload_drafts.job_id"],
            name="fk_draft_revisions_draft",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "edited_by"],
            ["users.account_id", "users.id"],
            name="fk_draft_revisions_editor",
        ),
        sa.CheckConstraint(VERSION_POSITIVE, name="ck_draft_revisions_version_positive"),
        sa.CheckConstraint(TEXT_SHA256_FORMAT, name="ck_draft_revisions_text_sha256_format"),
    )
    op.create_index("ix_draft_revisions_account_id", "draft_revisions", ["account_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.Enum(*AUDIT_ACTION, name="audit_action"), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), server_default="{}", nullable=False),
        *_timestamps(),
        # No cascade on either link: an audited user or job cannot be deleted on its own.
        sa.ForeignKeyConstraint(
            ["account_id", "user_id"], ["users.account_id", "users.id"], name="fk_audit_events_user"
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "job_id"], ["jobs.account_id", "jobs.id"], name="fk_audit_events_job"
        ),
        sa.CheckConstraint(JOB_MATCHES_ACTION, name="ck_audit_events_job_matches_action"),
        sa.CheckConstraint(DETAIL_IS_OBJECT, name="ck_audit_events_detail_is_object"),
    )
    op.create_index("ix_audit_events_account_id", "audit_events", ["account_id"])


def downgrade() -> None:
    # Every edit, review decision, audit event and job cost is lost; drafts and jobs stay.
    op.drop_table("audit_events")
    op.drop_table("draft_revisions")
    op.drop_constraint("ck_upload_drafts_approved_version_not_negative", "upload_drafts")
    op.drop_constraint("ck_upload_drafts_review_matches_status", "upload_drafts")
    op.drop_constraint("fk_upload_drafts_reviewer", "upload_drafts", type_="foreignkey")
    op.drop_constraint("uq_upload_drafts_account_job_id", "upload_drafts", type_="unique")
    for column in (
        "approved_version",
        "reject_reason",
        "reviewed_at",
        "reviewed_by",
        "review_status",
    ):
        op.drop_column("upload_drafts", column)
    op.drop_constraint("ck_jobs_cost_usd_not_negative", "jobs")
    op.drop_column("jobs", "cost_usd")
    for enum_name in NEW_ENUMS:
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
