"""A new sign-up code: stored, counted toward the daily cap and put in an email, or refused
without writing anything."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api.email_codes import hash_code
from src.api.email_verification import (
    CodeTooSoonError,
    MailCapReachedError,
    prepare_verify_email,
)
from src.api.mail import CODE_SUBJECTS, MailMessage
from src.api.signup import create_account_with_admin
from src.config.settings import ApiSettings
from src.db.models import EmailCode, EmailCodePurpose, MailSend, User
from tests.api.conftest import API_SECRET, api_settings

PASSWORD = "a-made-up-password"  # noqa: S105  (made up)
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _user(session: Session) -> User:
    return create_account_with_admin(
        session, account_name="One", email="ann@x.org", password=PASSWORD, verified_at=None
    )


def _prepare(
    session: Session, user: User, at: datetime = NOW, settings: ApiSettings | None = None
) -> MailMessage:
    return prepare_verify_email(
        session,
        account_id=user.account_id,
        user_id=user.id,
        settings=settings or api_settings(),
        now=at,
    )


def _counts(session: Session) -> tuple[int | None, int | None]:
    session.expire_all()
    return (
        session.scalar(select(func.count()).select_from(EmailCode)),
        session.scalar(select(func.count()).select_from(MailSend)),
    )


def test_stores_the_code_counts_the_send_and_returns_the_email(session: Session) -> None:
    user = _user(session)
    message = _prepare(session, user)
    assert (message.to, message.subject) == (
        "ann@x.org",
        CODE_SUBJECTS[EmailCodePurpose.VERIFY_EMAIL],
    )
    found = re.search(r"^ {4}([0-9]{6})$", message.body, re.MULTILINE)
    assert found is not None
    row = session.scalars(select(EmailCode)).one()
    assert row.code_hash == hash_code(
        EmailCodePurpose.VERIFY_EMAIL, user.id, found.group(1), secret=API_SECRET
    )
    assert "10 minutes" in message.body
    assert _counts(session) == (1, 1)


def test_a_second_code_within_the_gap_is_refused_without_writing(session: Session) -> None:
    user = _user(session)
    _prepare(session, user)
    with pytest.raises(CodeTooSoonError) as caught:
        _prepare(session, user, NOW + timedelta(seconds=20))
    assert caught.value.retry_after == 40
    assert _counts(session) == (1, 1)
    _prepare(session, user, NOW + timedelta(seconds=60))
    assert _counts(session) == (2, 2)


def test_over_the_daily_cap_nothing_is_written(session: Session) -> None:
    user = _user(session)
    settings = api_settings().model_copy(update={"mail_daily_limit": 1})
    session.add(MailSend(purpose=EmailCodePurpose.PASSWORD_RESET, created_at=NOW, updated_at=NOW))
    session.flush()
    with pytest.raises(MailCapReachedError):
        _prepare(session, user, settings=settings)
    assert _counts(session) == (0, 1)
