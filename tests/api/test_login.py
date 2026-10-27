"""Login finds the user by email and checks the password; an unknown email costs the same."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from src.api import login
from src.api.login import authenticate, find_user_by_email
from src.api.signup import create_account_with_admin

# The users made here count as having confirmed their email.
VERIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
WRONG_PASSWORD = "wrong-password"  # noqa: S105  (made up)


def test_right_password_returns_the_user(session: Session) -> None:
    user = create_account_with_admin(
        session, account_name="One", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    assert authenticate(session, email="a@x.org", password=PASSWORD) is user


def test_wrong_password_returns_none(session: Session) -> None:
    create_account_with_admin(
        session, account_name="One", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    assert authenticate(session, email="a@x.org", password=PASSWORD + "!") is None


def test_unknown_email_returns_none_after_the_dummy_check(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[str] = []
    monkeypatch.setattr(login, "verify_dummy_password", checked.append)
    assert authenticate(session, email="nobody@x.org", password=PASSWORD) is None
    assert checked == [PASSWORD]


@pytest.mark.parametrize("password", [PASSWORD, ""])
def test_a_user_with_no_password_is_refused_after_the_dummy_check(
    session: Session, monkeypatch: pytest.MonkeyPatch, password: str
) -> None:
    user = create_account_with_admin(
        session, account_name="One", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    user.password_hash = None
    session.flush()
    checked: list[str] = []
    monkeypatch.setattr(login, "verify_dummy_password", checked.append)
    assert authenticate(session, email="a@x.org", password=password) is None
    assert checked == [password]


def test_known_email_skips_the_dummy_check(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_account_with_admin(
        session, account_name="One", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    checked: list[str] = []
    monkeypatch.setattr(login, "verify_dummy_password", checked.append)
    authenticate(session, email="a@x.org", password=WRONG_PASSWORD)
    assert checked == []


def test_the_email_lookup_finds_the_user_in_any_account_or_none(session: Session) -> None:
    create_account_with_admin(
        session, account_name="One", email="a@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    other = create_account_with_admin(
        session, account_name="Two", email="b@x.org", password=PASSWORD, verified_at=VERIFIED_AT
    )
    assert find_user_by_email(session, "b@x.org") is other
    assert find_user_by_email(session, "nobody@x.org") is None
