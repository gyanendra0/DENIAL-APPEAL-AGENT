"""evidence_source: the new value cms_manual, for the CMS manual chapters

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_EVIDENCE_SOURCE = "cms_manual"
# The values the type had before this migration; the downgrade builds the type from them.
EVIDENCE_SOURCE_BEFORE = ("cms_ncd",)
OLD_TYPE_NAME = "evidence_source_old"


def upgrade() -> None:
    # Postgres refuses to USE a new enum value in the transaction that added it to an existing
    # type, so this migration only adds the value and writes no row.
    op.execute(f"ALTER TYPE evidence_source ADD VALUE '{NEW_EVIDENCE_SOURCE}'")


def downgrade() -> None:
    # Postgres cannot remove one enum value, so the type is built again without it. The rows
    # of the removed source go first (their chunks and vectors go with them, by cascade).
    op.execute(
        sa.text("DELETE FROM evidence_documents WHERE source::text = :source").bindparams(
            source=NEW_EVIDENCE_SOURCE
        )
    )
    op.execute(f"ALTER TYPE evidence_source RENAME TO {OLD_TYPE_NAME}")
    sa.Enum(*EVIDENCE_SOURCE_BEFORE, name="evidence_source").create(op.get_bind())
    op.execute(
        "ALTER TABLE evidence_documents ALTER COLUMN source TYPE evidence_source"
        " USING source::text::evidence_source"
    )
    op.execute(f"DROP TYPE {OLD_TYPE_NAME}")
