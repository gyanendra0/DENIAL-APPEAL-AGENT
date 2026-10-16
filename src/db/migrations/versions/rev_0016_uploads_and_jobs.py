"""uploads and jobs: an uploaded denial letter, its background job and the job's results

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-16
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPLOAD_FILE_TYPE = ("text", "pdf")
JOB_STATUS = ("queued", "running", "done", "failed")
JOB_FAILURE = (
    "extraction_failed",
    "budget_exceeded",
    "llm_unavailable",
    "evidence_not_ready",
    "interrupted",
    "internal_error",
)

TEXT_SHA256_FORMAT = "text_sha256 ~ '^[0-9a-f]{64}$'"
FILE_NAME_NOT_EMPTY = "file_name <> ''"
BYTE_SIZE_POSITIVE = "byte_size > 0"
TEXT_NOT_EMPTY = "text <> ''"
FAILURE_ONLY_WHEN_FAILED = "(status = 'failed') = (failure IS NOT NULL)"
END_STATE_ONLY_WHEN_DONE = "(status = 'done') = (end_state IS NOT NULL)"
TIMES_MATCH_STATUS = (
    "(status = 'queued' AND started_at IS NULL AND finished_at IS NULL)"
    " OR (status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL)"
    " OR (status = 'done' AND started_at IS NOT NULL AND finished_at IS NOT NULL)"
    " OR (status = 'failed' AND finished_at IS NOT NULL)"
)
FINISHED_NOT_BEFORE_STARTED = "finished_at >= started_at"
PROMPT_VERSION_NOT_EMPTY = "prompt_version <> ''"
MODEL_NAME_NOT_EMPTY = "model_name <> ''"
AGENT_VERSION_NOT_EMPTY = "agent_version <> ''"
SEARCH_QUESTION_VERSION_NOT_EMPTY = "search_question_version <> ''"
# IS NOT DISTINCT FROM, so a trace without the key is refused too (`=` would give NULL).
TRACE_END_STATE_MATCHES = "(trace ->> 'end_state') IS NOT DISTINCT FROM end_state::text"
TRACE_AGENT_VERSION_MATCHES = "(trace ->> 'agent_version') IS NOT DISTINCT FROM agent_version"

# Made by earlier migrations; used here, never created or dropped here.
AGENT_END_STATE = postgresql.ENUM(name="agent_end_state", create_type=False)
LLM_PROVIDER = postgresql.ENUM(name="llm_provider", create_type=False)

NEW_ENUMS = ("job_failure", "job_status", "upload_file_type")


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


def _job_link(table: str) -> sa.ForeignKeyConstraint:
    # The link carries `account_id`, so a result never names another account's job.
    return sa.ForeignKeyConstraint(
        ["account_id", "job_id"],
        ["jobs.account_id", "jobs.id"],
        name=f"fk_{table}_job",
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_unique_constraint("uq_users_account_id", "users", ["account_id", "id"])

    op.create_table(
        "uploads",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_type", sa.Enum(*UPLOAD_FILE_TYPE, name="upload_file_type"), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("account_id", "id", name="uq_uploads_account_id"),
        # No cascade: a user who still has uploads cannot be deleted by accident.
        sa.ForeignKeyConstraint(
            ["account_id", "user_id"], ["users.account_id", "users.id"], name="fk_uploads_user"
        ),
        sa.CheckConstraint(FILE_NAME_NOT_EMPTY, name="ck_uploads_file_name_not_empty"),
        sa.CheckConstraint(BYTE_SIZE_POSITIVE, name="ck_uploads_byte_size_positive"),
        sa.CheckConstraint(TEXT_NOT_EMPTY, name="ck_uploads_text_not_empty"),
        sa.CheckConstraint(TEXT_SHA256_FORMAT, name="ck_uploads_text_sha256_format"),
    )
    op.create_index("ix_uploads_account_id", "uploads", ["account_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("upload_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum(*JOB_STATUS, name="job_status"), nullable=False),
        sa.Column("failure", sa.Enum(*JOB_FAILURE, name="job_failure"), nullable=True),
        sa.Column("end_state", AGENT_END_STATE, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("account_id", "id", name="uq_jobs_account_id"),
        sa.UniqueConstraint("upload_id", name="uq_jobs_upload_id"),
        sa.ForeignKeyConstraint(
            ["account_id", "upload_id"],
            ["uploads.account_id", "uploads.id"],
            name="fk_jobs_upload",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(FAILURE_ONLY_WHEN_FAILED, name="ck_jobs_failure_only_when_failed"),
        sa.CheckConstraint(END_STATE_ONLY_WHEN_DONE, name="ck_jobs_end_state_only_when_done"),
        sa.CheckConstraint(TIMES_MATCH_STATUS, name="ck_jobs_times_match_status"),
        sa.CheckConstraint(FINISHED_NOT_BEFORE_STARTED, name="ck_jobs_finished_not_before_started"),
    )
    op.create_index("ix_jobs_account_id", "jobs", ["account_id"])

    op.create_table(
        "upload_extractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("provider", LLM_PROVIDER, nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("job_id", name="uq_upload_extractions_job_id"),
        _job_link("upload_extractions"),
        sa.CheckConstraint(
            PROMPT_VERSION_NOT_EMPTY, name="ck_upload_extractions_prompt_version_not_empty"
        ),
        sa.CheckConstraint(MODEL_NAME_NOT_EMPTY, name="ck_upload_extractions_model_name_not_empty"),
        sa.CheckConstraint(TEXT_SHA256_FORMAT, name="ck_upload_extractions_text_sha256_format"),
    )
    op.create_index("ix_upload_extractions_account_id", "upload_extractions", ["account_id"])

    op.create_table(
        "upload_agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("agent_version", sa.String(40), nullable=False),
        sa.Column("end_state", AGENT_END_STATE, nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("trace", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("job_id", name="uq_upload_agent_runs_job_id"),
        _job_link("upload_agent_runs"),
        sa.CheckConstraint(
            AGENT_VERSION_NOT_EMPTY, name="ck_upload_agent_runs_agent_version_not_empty"
        ),
        sa.CheckConstraint(TEXT_SHA256_FORMAT, name="ck_upload_agent_runs_text_sha256_format"),
        sa.CheckConstraint(
            TRACE_END_STATE_MATCHES, name="ck_upload_agent_runs_trace_end_state_matches"
        ),
        sa.CheckConstraint(
            TRACE_AGENT_VERSION_MATCHES, name="ck_upload_agent_runs_trace_agent_version_matches"
        ),
    )
    op.create_index("ix_upload_agent_runs_account_id", "upload_agent_runs", ["account_id"])

    op.create_table(
        "upload_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        _account_fk(),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("search_question_version", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("provider", LLM_PROVIDER, nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("letter", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("job_id", name="uq_upload_drafts_job_id"),
        _job_link("upload_drafts"),
        sa.CheckConstraint(
            PROMPT_VERSION_NOT_EMPTY, name="ck_upload_drafts_prompt_version_not_empty"
        ),
        sa.CheckConstraint(
            SEARCH_QUESTION_VERSION_NOT_EMPTY,
            name="ck_upload_drafts_search_question_version_not_empty",
        ),
        sa.CheckConstraint(MODEL_NAME_NOT_EMPTY, name="ck_upload_drafts_model_name_not_empty"),
        sa.CheckConstraint(TEXT_SHA256_FORMAT, name="ck_upload_drafts_text_sha256_format"),
    )
    op.create_index("ix_upload_drafts_account_id", "upload_drafts", ["account_id"])


def downgrade() -> None:
    # Every upload, job and result is lost. `agent_end_state` and `llm_provider` stay.
    for table in ("upload_drafts", "upload_agent_runs", "upload_extractions", "jobs", "uploads"):
        op.drop_table(table)
    for enum_name in NEW_ENUMS:
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
    op.drop_constraint("uq_users_account_id", "users", type_="unique")
