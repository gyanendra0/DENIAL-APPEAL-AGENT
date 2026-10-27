"""A new sign-up code for one user: the limits checked, the code stored, the email prepared.

Used by sign-up, by a login with the right password and an unverified email, and by "send a new
code". The caller sends the returned email after its answer, so the code row and the count of
sent emails are committed first.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from src.api.email_codes import add_code, new_code, resend_wait_seconds
from src.api.mail import MailMessage, code_email
from src.api.mail_cap import reserve_mail_send
from src.config.settings import ApiSettings
from src.db.models import EmailCodePurpose, User
from src.db.query import for_account

logger = logging.getLogger(__name__)

PURPOSE = EmailCodePurpose.VERIFY_EMAIL
# The 503 sentence when the daily cap stops a code from being sent.
MAIL_UNAVAILABLE = "we cannot send email right now; try again later"


class CodeTooSoonError(Exception):
    """The user got a code too recently, or too many this hour."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"a new code is allowed in {retry_after} seconds")
        self.retry_after = retry_after


class MailCapReachedError(Exception):
    """Today's emails have reached `MAIL_DAILY_LIMIT`; nothing was sent or stored."""


def prepare_verify_email(
    session: Session, *, account_id: int, user_id: int, settings: ApiSettings, now: datetime
) -> MailMessage:
    """Store a new sign-up code for the user and return the email that carries it.

    Locks the user row, so two requests at the same time cannot both pass the limits. Raises
    `CodeTooSoonError` inside the resend gap or the hourly cap, and `MailCapReachedError` over the
    daily cap; either way nothing is written. Does not commit.
    """
    user = session.scalars(
        for_account(User, account_id).where(User.id == user_id).with_for_update()
    ).one()
    wait = resend_wait_seconds(
        session,
        account_id=account_id,
        user_id=user_id,
        purpose=PURPOSE,
        now=now,
        gap_seconds=settings.email_code_resend_seconds,
        per_hour=settings.email_code_per_user_per_hour,
    )
    if wait is not None:
        raise CodeTooSoonError(wait)
    if not reserve_mail_send(session, PURPOSE, now=now, daily_limit=settings.mail_daily_limit):
        raise MailCapReachedError
    code = new_code()
    add_code(
        session,
        account_id=account_id,
        user_id=user_id,
        purpose=PURPOSE,
        code=code,
        secret=settings.auth_token_secret.get_secret_value(),
        ttl_minutes=settings.email_code_ttl_minutes,
        now=now,
    )
    logger.info("sign-up code made: account %d, user %d", account_id, user_id)
    return code_email(
        PURPOSE, to=user.email, code=code, ttl_minutes=settings.email_code_ttl_minutes
    )
