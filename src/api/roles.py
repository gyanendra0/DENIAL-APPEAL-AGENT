"""Who may do what: the role a user acts with, and the roles each kind of action needs.

`ADMIN_EMAILS` is the only source of `admin`. The role is worked out on every request, so a
change to the setting takes effect at the next restart, with no database edit: a listed email is
`admin`, and a stored `admin` that is no longer listed acts as `reviewer`. The API decides; a
screen only hides what a role cannot do.
"""

from src.config.settings import ApiSettings
from src.db.models import UserRole

# Every role may upload a letter and read the jobs it may see.
UPLOAD_ROLES = frozenset({UserRole.VIEWER, UserRole.REVIEWER, UserRole.ADMIN})
# Editing a draft's text.
EDIT_ROLES = frozenset({UserRole.REVIEWER, UserRole.ADMIN})
# Approving or rejecting a draft.
DECIDE_ROLES = frozenset({UserRole.ADMIN})


def effective_role(settings: ApiSettings, email: str, stored: UserRole) -> UserRole:
    """The role a user with `email` (as stored: trimmed, lowercased) and `stored` role acts with."""
    if email in settings.admin_email_set:
        return UserRole.ADMIN
    if stored is UserRole.ADMIN:
        return UserRole.REVIEWER
    return stored
