"""Who may do what: the role a user acts with, and the roles each kind of action needs.

`ADMIN_EMAILS` is the only source of `admin` in the shared account; an individual is `admin` of
their own personal account (and so still reads only that account). The role is worked out on
every request, so a change to the setting takes effect at the next restart, with no database
edit: a listed email is `admin`, and a stored `admin` that is no longer listed acts as `reviewer`.
The API decides; a screen only hides what a role cannot do. An admin reads every job of the
account; any other role reads only the jobs of their own uploads (`job_scope` in
`src/api/dependencies.py`).
"""

from src.config.settings import ApiSettings
from src.db.models import AccountKind, UserRole

# Every role may upload a letter and read the jobs it may see.
UPLOAD_ROLES = frozenset({UserRole.VIEWER, UserRole.REVIEWER, UserRole.ADMIN})
# Editing a draft's text.
EDIT_ROLES = frozenset({UserRole.REVIEWER, UserRole.ADMIN})
# Approving or rejecting a draft.
DECIDE_ROLES = frozenset({UserRole.ADMIN})


def effective_role(
    settings: ApiSettings, email: str, stored: UserRole, kind: AccountKind
) -> UserRole:
    """The role a user with `email` (as stored: trimmed, lowercased) and `stored` role acts with
    in an account of `kind`."""
    if kind is AccountKind.PERSONAL or email in settings.admin_email_set:
        return UserRole.ADMIN
    if stored is UserRole.ADMIN:
        return UserRole.REVIEWER
    return stored
