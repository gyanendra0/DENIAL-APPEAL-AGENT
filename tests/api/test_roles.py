"""The role a user acts with comes from `ADMIN_EMAILS`, and what each role may do."""

import pytest

from src.api.roles import DECIDE_ROLES, EDIT_ROLES, UPLOAD_ROLES, effective_role
from src.db.models import UserRole
from tests.api.conftest import api_settings

# The test settings list `admin@example.test` only.
SETTINGS = api_settings()


@pytest.mark.parametrize("stored", list(UserRole))
def test_a_listed_email_acts_as_admin_whatever_is_stored(stored: UserRole) -> None:
    assert effective_role(SETTINGS, "admin@example.test", stored) is UserRole.ADMIN


@pytest.mark.parametrize(
    ("stored", "acts_as"),
    [
        (UserRole.ADMIN, UserRole.REVIEWER),
        (UserRole.REVIEWER, UserRole.REVIEWER),
        (UserRole.VIEWER, UserRole.VIEWER),
    ],
)
def test_an_unlisted_email_keeps_its_stored_role_but_never_admin(
    stored: UserRole, acts_as: UserRole
) -> None:
    assert effective_role(SETTINGS, "ann@x.org", stored) is acts_as


def test_the_list_is_compared_lowercased_like_a_stored_email() -> None:
    listed = SETTINGS.model_copy(update={"admin_emails": " Ann@X.org "})
    assert effective_role(listed, "ann@x.org", UserRole.VIEWER) is UserRole.ADMIN


@pytest.mark.parametrize(
    ("role", "uploads", "edits", "decides"),
    [
        (UserRole.VIEWER, True, False, False),
        (UserRole.REVIEWER, True, True, False),
        (UserRole.ADMIN, True, True, True),
    ],
)
def test_every_role_uploads_reviewers_and_admins_edit_only_admins_decide(
    role: UserRole, uploads: bool, edits: bool, decides: bool
) -> None:
    assert (role in UPLOAD_ROLES, role in EDIT_ROLES, role in DECIDE_ROLES) == (
        uploads,
        edits,
        decides,
    )
