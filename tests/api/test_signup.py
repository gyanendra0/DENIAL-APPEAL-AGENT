"""Sign-up adds the user to the one shared account, only for allowed emails, and audits the
joining only once the email is confirmed."""

import threading
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api import signup
from src.api.email_codes import add_code
from src.api.login import find_user_by_email
from src.api.passwords import verify_password
from src.api.signup import (
    EmailTakenError,
    create_member,
    may_sign_up,
    record_joined,
    shared_account_id,
)
from src.config.settings import ApiSettings
from src.db.models import (
    Account,
    AuditAction,
    AuditEvent,
    EmailCode,
    EmailCodePurpose,
    User,
    UserRole,
)
from tests.api.conftest import api_settings

# The users made here count as having confirmed their email.
VERIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
ADMIN = "admin@example.test"
SHARED = "Example Clinic"


def _settings(**update: str) -> ApiSettings:
    """The test settings (admin `ADMIN`, account `SHARED`, no allow-list) with `update` applied."""
    return api_settings().model_copy(update={"signup_allowed_domains": "", **update})


ALLOW_X = _settings(signup_allowed_domains="x.org")


def _counts(session: Session) -> tuple[int, int]:
    accounts = session.scalar(select(func.count()).select_from(Account))
    users = session.scalar(select(func.count()).select_from(User))
    assert accounts is not None and users is not None
    return accounts, users


def _member(session: Session, email: str, verified_at: datetime | None = VERIFIED_AT) -> User:
    return create_member(
        session, settings=ALLOW_X, email=email, password=PASSWORD, verified_at=verified_at
    )


def _actions(session: Session) -> list[tuple[int, AuditAction]]:
    rows = session.execute(select(AuditEvent.user_id, AuditEvent.action).order_by(AuditEvent.id))
    return [(user_id, action) for user_id, action in rows]


@pytest.mark.parametrize(
    ("settings", "email", "allowed"),
    [
        (_settings(), ADMIN, True),
        (_settings(), "ann@x.org", False),
        (_settings(signup_allowed_emails="ann@x.org"), "ann@x.org", True),
        (_settings(signup_allowed_emails="ann@x.org"), "bob@x.org", False),
        (ALLOW_X, "bob@x.org", True),
        (ALLOW_X, "bob@sub.x.org", False),
        (ALLOW_X, "bob@notx.org", False),
    ],
)
def test_only_admins_and_allowed_emails_or_domains_may_sign_up(
    settings: ApiSettings, email: str, allowed: bool
) -> None:
    assert may_sign_up(settings, email) is allowed


def test_the_first_member_creates_the_shared_account_with_a_hashed_password(
    session: Session,
) -> None:
    user = _member(session, "ann@x.org")
    account = session.get(Account, user.account_id)
    assert account is not None and account.name == SHARED
    assert (user.email, user.role) == ("ann@x.org", UserRole.REVIEWER)
    assert user.password_hash is not None and user.password_hash != PASSWORD
    assert verify_password(user.password_hash, PASSWORD)


def test_later_members_join_the_same_account(session: Session) -> None:
    ann = _member(session, "ann@x.org")
    admin = _member(session, ADMIN)
    assert admin.account_id == ann.account_id
    # Stored as reviewer too: `ADMIN_EMAILS` makes the admin on each request.
    assert admin.role == UserRole.REVIEWER
    assert _counts(session) == (1, 2)


def test_an_existing_account_of_that_name_is_joined_not_added_again(session: Session) -> None:
    """The path a sign-up takes when another one added the account a moment before."""
    first = shared_account_id(session, SHARED)
    assert shared_account_id(session, SHARED) == first
    assert _member(session, "ann@x.org").account_id == first
    assert _counts(session) == (1, 1)


def test_a_google_only_member_has_no_password(session: Session) -> None:
    user = create_member(
        session, settings=ALLOW_X, email="ann@x.org", password=None, verified_at=VERIFIED_AT
    )
    assert user.password_hash is None


def test_taken_email_raises_and_writes_nothing(session: Session) -> None:
    _member(session, "a@x.org")
    before = _counts(session)
    with pytest.raises(EmailTakenError) as caught:
        _member(session, "a@x.org")
    assert _counts(session) == before
    assert "a@x.org" not in str(caught.value)


def test_other_database_errors_propagate(session: Session) -> None:
    # An email that is not lowercased breaks `ck_users_email_normalized`, not a unique key.
    with pytest.raises(IntegrityError):
        _member(session, "A@x.org")


def _unverified(session: Session, email: str = "a@x.org") -> User:
    user = _member(session, email, verified_at=None)
    add_code(
        session,
        account_id=user.account_id,
        user_id=user.id,
        purpose=EmailCodePurpose.VERIFY_EMAIL,
        code="123456",
        secret="a-made-up-secret",  # noqa: S106  (made up)
        ttl_minutes=10,
        now=VERIFIED_AT,
    )
    return user


def test_a_new_user_is_unverified_unless_told_when_it_was_verified(session: Session) -> None:
    fresh = _member(session, "a@x.org", verified_at=None)
    vouched = _member(session, "b@x.org")
    assert (fresh.email_verified_at, vouched.email_verified_at) == (None, VERIFIED_AT)


def test_an_unverified_holder_is_replaced_and_the_shared_account_stays_intact(
    session: Session,
) -> None:
    """A second sign-up for the same unverified email deletes only that user and its code."""
    other = _member(session, "b@x.org")
    record_joined(session, other)
    squatter = _unverified(session)
    old_user_id = squatter.id
    user = _member(session, "a@x.org", verified_at=None)
    session.expire_all()
    assert session.get(User, old_user_id) is None
    assert user.account_id == other.account_id
    assert _counts(session) == (1, 2)
    assert sorted(session.scalars(select(User.email))) == ["a@x.org", "b@x.org"]
    # The other member and the account's audit row stay; the squatter's code went with it.
    assert _actions(session) == [(other.id, AuditAction.ACCOUNT_CREATED)]
    assert session.scalars(select(EmailCode)).all() == []


def test_a_verified_holder_of_the_email_is_not_replaced(session: Session) -> None:
    _member(session, "a@x.org")
    with pytest.raises(EmailTakenError):
        _member(session, "a@x.org", verified_at=None)
    assert _counts(session) == (1, 1)


def test_a_holder_verified_just_before_the_lock_is_not_replaced(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lookup sees an unverified holder, but its code is confirmed before the row is locked."""
    squatter = _unverified(session)

    def find_then_verify(found_in: Session, email: str) -> User | None:
        holder = find_user_by_email(found_in, email)
        found_in.execute(
            update(User)
            .where(User.email == email)
            .values(email_verified_at=VERIFIED_AT)
            .execution_options(synchronize_session=False)
        )
        return holder

    monkeypatch.setattr(signup, "find_user_by_email", find_then_verify)
    with pytest.raises(EmailTakenError):
        _member(session, "a@x.org", verified_at=None)
    session.expire_all()
    assert session.scalars(select(User.id)).all() == [squatter.id]


def test_the_first_confirmed_user_creates_the_account_and_later_ones_join(
    session: Session,
) -> None:
    ann = _member(session, "ann@x.org")
    bob = _member(session, "bob@x.org")
    record_joined(session, ann)
    record_joined(session, bob)
    session.flush()
    assert _actions(session) == [
        (ann.id, AuditAction.ACCOUNT_CREATED),
        (bob.id, AuditAction.USER_JOINED),
    ]


def test_the_first_to_confirm_creates_the_account_not_the_first_to_sign_up(
    session: Session,
) -> None:
    early = _member(session, "ann@x.org", verified_at=None)
    late = _member(session, "bob@x.org")
    record_joined(session, late)
    session.flush()
    assert _actions(session) == [(late.id, AuditAction.ACCOUNT_CREATED)]
    assert early.id != late.id


def test_sign_up_itself_writes_no_audit_event(session: Session) -> None:
    _member(session, "ann@x.org", verified_at=None)
    _member(session, "bob@x.org")
    assert _actions(session) == []


def _waits_on_a_lock(engine: Engine, pid: int, timeout: float = 5.0) -> bool:
    """True once backend `pid` waits on a lock; False after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.scalar(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            if waiting:
                return True
            time.sleep(0.02)
    return False


def test_two_sign_ups_at_the_same_moment_join_one_account(engine: Engine) -> None:
    """The second insert waits on the unique key, then reads the first one's account."""
    name = "Made-up Clinic Signing Up Twice"
    found: list[int] = []
    try:
        with Session(engine) as first, Session(engine) as second:
            first_id = shared_account_id(first, name)
            second_pid = second.scalar(text("SELECT pg_backend_pid()"))
            assert second_pid is not None
            worker = threading.Thread(target=lambda: found.append(shared_account_id(second, name)))
            worker.start()
            assert _waits_on_a_lock(engine, second_pid)
            first.commit()
            worker.join(timeout=5)
            second.rollback()
        assert found == [first_id]
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(delete(Account).where(Account.name == name))
            cleanup.commit()
