"""evidence_documents and evidence_chunks: public policy text, cut into citable chunks

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVIDENCE_SOURCE = ("cms_ncd",)
SOURCE_DOCUMENT_ID_NOT_EMPTY = "source_document_id <> ''"
SECTION_NUMBER_NOT_EMPTY = "section_number <> ''"
TITLE_NOT_EMPTY = "title <> ''"
VERSION_NUMBER_POSITIVE = "version_number >= 1"
CHUNK_INDEX_NOT_NEGATIVE = "chunk_index >= 0"
SECTION_TITLE_NOT_EMPTY = "section_title <> ''"
TEXT_NOT_EMPTY = "text <> ''"
TEXT_SHA256_FORMAT = "text_sha256 ~ '^[0-9a-f]{64}$'"
CHUNKER_VERSION_NOT_EMPTY = "chunker_version <> ''"


def upgrade() -> None:
    op.create_table(
        "evidence_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.Enum(*EVIDENCE_SOURCE, name="evidence_source"), nullable=False),
        sa.Column("source_document_id", sa.String(40), nullable=False),
        sa.Column("section_number", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("source_file_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "source", "source_document_id", name="uq_evidence_documents_source_document"
        ),
        sa.UniqueConstraint(
            "source", "section_number", name="uq_evidence_documents_source_section"
        ),
        sa.CheckConstraint(
            SOURCE_DOCUMENT_ID_NOT_EMPTY,
            name="ck_evidence_documents_source_document_id_not_empty",
        ),
        sa.CheckConstraint(
            SECTION_NUMBER_NOT_EMPTY, name="ck_evidence_documents_section_number_not_empty"
        ),
        sa.CheckConstraint(TITLE_NOT_EMPTY, name="ck_evidence_documents_title_not_empty"),
        sa.CheckConstraint(
            VERSION_NUMBER_POSITIVE, name="ck_evidence_documents_version_number_positive"
        ),
    )
    op.create_table(
        "evidence_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evidence_document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.String(100), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("chunker_version", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["evidence_document_id"],
            ["evidence_documents.id"],
            name="fk_evidence_chunks_document",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "evidence_document_id", "chunk_index", name="uq_evidence_chunks_document_index"
        ),
        sa.CheckConstraint(
            CHUNK_INDEX_NOT_NEGATIVE, name="ck_evidence_chunks_chunk_index_not_negative"
        ),
        sa.CheckConstraint(
            SECTION_TITLE_NOT_EMPTY, name="ck_evidence_chunks_section_title_not_empty"
        ),
        sa.CheckConstraint(TEXT_NOT_EMPTY, name="ck_evidence_chunks_text_not_empty"),
        sa.CheckConstraint(TEXT_SHA256_FORMAT, name="ck_evidence_chunks_text_sha256_format"),
        sa.CheckConstraint(
            CHUNKER_VERSION_NOT_EMPTY, name="ck_evidence_chunks_chunker_version_not_empty"
        ),
    )


def downgrade() -> None:
    op.drop_table("evidence_chunks")
    op.drop_table("evidence_documents")
    sa.Enum(name="evidence_source").drop(op.get_bind(), checkfirst=True)
