"""users: a password hash, and one email across all accounts (stored trimmed and lowercased)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `\s` and not `btrim`: btrim removes spaces only, a tab or a newline would pass.
EMAIL_NORMALIZED = "email = lower(email) AND email !~ '^\\s|\\s$'"


def upgrade() -> None:
    # No data step: `users` had no rows when this was written (checked on `app` 2026-10-16).
    # The non-null column without a default makes this upgrade fail if a user row exists.
    op.drop_constraint("uq_users_account_email", "users", type_="unique")
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_check_constraint("ck_users_email_normalized", "users", EMAIL_NORMALIZED)
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=False))


def downgrade() -> None:
    # Cannot fail: an email unique across all accounts is also unique within each account.
    # The stored password hashes are lost.
    op.drop_column("users", "password_hash")
    op.drop_constraint("ck_users_email_normalized", "users", type_="check")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.create_unique_constraint("uq_users_account_email", "users", ["account_id", "email"])
