"""generated_documents: noise version and noise record of each document

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "generated_documents"
NOISE_VERSION_NOT_EMPTY = "noise_version <> ''"
NOISE_VERSION_CHECK = "ck_generated_documents_noise_version_not_empty"
DELETE_DOCUMENTS = "DELETE FROM generated_documents"


def upgrade() -> None:
    # The documents are derived data and every run replaces them. Rows written before the
    # noise step have no noise record, so they are removed instead of given a made-up one.
    op.execute(sa.text(DELETE_DOCUMENTS))
    op.add_column(TABLE, sa.Column("noise_version", sa.String(20), nullable=False))
    op.add_column(TABLE, sa.Column("noise_record", postgresql.JSONB(), nullable=False))
    op.create_check_constraint(NOISE_VERSION_CHECK, TABLE, NOISE_VERSION_NOT_EMPTY)


def downgrade() -> None:
    # Without the two columns nothing would say that a stored text is noisy, so the rows go too.
    op.execute(sa.text(DELETE_DOCUMENTS))
    op.drop_constraint(NOISE_VERSION_CHECK, TABLE, type_="check")
    op.drop_column(TABLE, "noise_record")
    op.drop_column(TABLE, "noise_version")
