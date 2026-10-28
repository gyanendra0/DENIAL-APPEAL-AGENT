"""Who a Google identity opens: linked users log in, linking needs an email Google vouches for,
and a Google account never opens two users."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api.google_identity import (
    GoogleRefusal,
    GoogleRefusedError,
    create_google_member,
    may_link,
    sign_in_with_google,
)
from src.api.google_oidc import GoogleIdentity
from src.api.signup import EmailTakenError
from src.db.models import (
    Account,
    AccountKind,
    AuditAction,
    AuditEvent,
    IdentityProvider,
    User,
    UserIdentity,
    UserRole,
)
from tests.api.conftest import add_member, add_old_unverified_admin, api_settings

# The users made here count as having confirmed their email.
VERIFIED_AT = datetime(2026, 1, 1, tzinfo=UTC)

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
SUB = "100000000000000000001"


def _identity(
    email: str = "ann@gmail.com",
    *,
    subject: str = SUB,
    verified: bool = True,
    hd: str | None = None,
) -> GoogleIdentity:
    return GoogleIdentity(subject=subject, email=email, email_verified=verified, hosted_domain=hd)


def _user(session: Session, email: str = "ann@gmail.com", name: str = "Clinic") -> User:
    return add_member(
        session, account_name=name, email=email, password=PASSWORD, verified_at=VERIFIED_AT
    )


def _count(
    session: Session, model: type[UserIdentity] | type[AuditEvent] | type[Account] | type[User]
) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _actions(session: Session) -> list[AuditAction]:
    return list(session.scalars(select(AuditEvent.action).order_by(AuditEvent.id)))


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (_identity("ann@gmail.com"), True),
        (_identity("ann@clinic.example", hd="clinic.example"), True),
        (_identity("ann@clinic.example"), False),
        (_identity("ann@gmail.com.example"), False),
        (_identity("ann@gmail.com", verified=False), False),
    ],
)
def test_only_an_email_google_vouches_for_may_link(
    identity: GoogleIdentity, expected: bool
) -> None:
    assert may_link(identity) is expected


def test_an_unverified_email_is_refused_before_any_lookup(session: Session) -> None:
    _user(session)
    with pytest.raises(GoogleRefusedError) as caught:
        sign_in_with_google(session, _identity(verified=False))
    assert caught.value.reason is GoogleRefusal.NOT_VERIFIED
    assert _count(session, UserIdentity) == 0


def test_an_email_with_no_user_opens_nobody_and_writes_nothing(session: Session) -> None:
    _user(session, "bob@gmail.com")
    assert sign_in_with_google(session, _identity("ann@gmail.com")) is None
    assert (_count(session, UserIdentity), _actions(session)) == (0, [])


def test_a_workspace_email_with_no_user_may_start_a_new_account(session: Session) -> None:
    assert (
        sign_in_with_google(session, _identity("ann@clinic.example", hd="clinic.example")) is None
    )


def test_a_plain_google_account_on_another_domain_with_no_user_must_sign_up(
    session: Session,
) -> None:
    with pytest.raises(GoogleRefusedError) as caught:
        sign_in_with_google(session, _identity("ann@clinic.example"))
    assert caught.value.reason is GoogleRefusal.USE_SIGNUP
    assert (_count(session, UserIdentity), _actions(session)) == (0, [])


def test_a_gmail_user_is_linked_once_and_audited(session: Session) -> None:
    user = _user(session)

    assert sign_in_with_google(session, _identity()) is user
    assert sign_in_with_google(session, _identity()) is user

    [link] = session.scalars(select(UserIdentity)).all()
    assert (link.account_id, link.user_id, link.provider) == (
        user.account_id,
        user.id,
        IdentityProvider.GOOGLE,
    )
    assert (link.subject, link.email_at_link) == (SUB, "ann@gmail.com")
    [event] = session.scalars(select(AuditEvent)).all()
    assert (event.account_id, event.user_id, event.action, event.job_id) == (
        user.account_id,
        user.id,
        AuditAction.GOOGLE_LINKED,
        None,
    )


def test_a_workspace_user_is_linked(session: Session) -> None:
    user = _user(session, "ann@clinic.example")
    assert (
        sign_in_with_google(session, _identity("ann@clinic.example", hd="clinic.example")) is user
    )


def test_a_plain_google_account_on_another_domain_must_use_the_password(session: Session) -> None:
    _user(session, "ann@clinic.example")
    with pytest.raises(GoogleRefusedError) as caught:
        sign_in_with_google(session, _identity("ann@clinic.example"))
    assert caught.value.reason is GoogleRefusal.USE_PASSWORD
    assert (_count(session, UserIdentity), _actions(session)) == (0, [])


def test_a_user_linked_to_another_google_account_must_use_the_password(session: Session) -> None:
    _user(session)
    sign_in_with_google(session, _identity())
    with pytest.raises(GoogleRefusedError) as caught:
        sign_in_with_google(session, _identity(subject="100000000000000000002"))
    assert caught.value.reason is GoogleRefusal.USE_PASSWORD


def test_a_google_account_never_links_to_a_second_user(session: Session) -> None:
    _user(session, "ann@gmail.com", "One")
    sign_in_with_google(session, _identity("ann@gmail.com"))
    _user(session, "ann.new@gmail.com", "Two")

    with pytest.raises(GoogleRefusedError) as caught:
        sign_in_with_google(session, _identity("ann.new@gmail.com"))

    assert caught.value.reason is GoogleRefusal.LINKED_ELSEWHERE
    assert _count(session, UserIdentity) == 1
    assert _actions(session) == [AuditAction.GOOGLE_LINKED]


def _google_member(session: Session, email: str = "ann@gmail.com", subject: str = SUB) -> User:
    return create_google_member(
        session,
        settings=api_settings(),
        kind=AccountKind.SHARED,
        subject=subject,
        email=email,
        now=VERIFIED_AT,
    )


def test_a_google_member_joins_the_shared_account_with_no_password_and_its_link(
    session: Session,
) -> None:
    user = _google_member(session)

    account = session.get(Account, user.account_id)
    assert account is not None and account.name == api_settings().shared_account_name
    assert (user.email, user.role, user.password_hash) == ("ann@gmail.com", UserRole.REVIEWER, None)
    [link] = session.scalars(select(UserIdentity)).all()
    assert (link.user_id, link.subject) == (user.id, SUB)
    assert _actions(session) == []


def test_a_personal_google_member_gets_a_new_personal_account_with_its_link(
    session: Session,
) -> None:
    user = create_google_member(
        session,
        settings=api_settings(),
        kind=AccountKind.PERSONAL,
        subject=SUB,
        email="ann@gmail.com",
        now=VERIFIED_AT,
    )

    account = session.get(Account, user.account_id)
    assert account is not None and account.kind is AccountKind.PERSONAL
    assert account.name != api_settings().shared_account_name
    assert (user.password_hash, user.email_verified_at) == (None, VERIFIED_AT)
    [link] = session.scalars(select(UserIdentity)).all()
    assert (link.user_id, link.subject) == (user.id, SUB)


def test_a_google_member_with_an_admin_email_is_stored_as_a_reviewer_too(
    session: Session,
) -> None:
    # `ADMIN_EMAILS` makes the admin on each request, not the stored role.
    assert _google_member(session, "admin@example.test").role == UserRole.REVIEWER


def test_a_google_account_already_linked_adds_nothing(session: Session) -> None:
    _google_member(session)
    before = (_count(session, User), _count(session, UserIdentity))

    with pytest.raises(GoogleRefusedError) as caught:
        _google_member(session, "ann.new@gmail.com")

    assert caught.value.reason is GoogleRefusal.LINKED_ELSEWHERE
    assert (_count(session, User), _count(session, UserIdentity)) == before


def test_a_taken_email_is_refused_like_sign_up(session: Session) -> None:
    _user(session, "ann@gmail.com", "Clinic")
    with pytest.raises(EmailTakenError):
        _google_member(session)
    assert _count(session, UserIdentity) == 0


def test_a_google_member_replaces_an_unverified_admin_of_an_older_account(
    session: Session,
) -> None:
    old = add_old_unverified_admin(session, account_name="Old Clinic", email="ann@gmail.com")
    old_account_id = old.account_id
    user = _google_member(session)
    session.expire_all()
    assert session.get(Account, old_account_id) is None
    assert (_count(session, Account), _count(session, User), _actions(session)) == (1, 1, [])
    assert user.email_verified_at == VERIFIED_AT


def test_an_unverified_user_with_the_email_is_not_linked(session: Session) -> None:
    squatter = add_member(
        session, account_name="Squatter", email="ann@gmail.com", password=PASSWORD, verified_at=None
    )
    assert sign_in_with_google(session, _identity()) is None
    assert (_count(session, UserIdentity), _actions(session)) == (0, [])
    assert session.get(User, squatter.id) is not None


def test_a_google_member_is_verified_and_replaces_only_an_unverified_holder(
    session: Session,
) -> None:
    other = _user(session, "bob@x.org", api_settings().shared_account_name)
    squatter = add_member(
        session,
        account_name=api_settings().shared_account_name,
        email="ann@gmail.com",
        password=PASSWORD,
        verified_at=None,
    )
    old_user_id = squatter.id
    user = _google_member(session)
    session.expire_all()
    assert user.email_verified_at == VERIFIED_AT
    assert session.get(User, old_user_id) is None
    assert user.account_id == other.account_id
    assert (_count(session, Account), _count(session, User)) == (1, 2)
