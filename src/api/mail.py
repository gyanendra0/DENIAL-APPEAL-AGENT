"""Outgoing email: the code messages and the two ways they leave the app.

`SmtpSender` sends through any provider's SMTP server (STARTTLS). `OutboxSender` writes each
message as a `.eml` file instead, for local development; the app refuses it with secure cookies,
so a deployed app cannot silently drop emails. Nothing here logs an address, a code or a body.
"""

import logging
import smtplib
import ssl
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from src.config.settings import ApiSettings
from src.db.models import EmailCodePurpose

logger = logging.getLogger(__name__)

# The sender written into outbox files when no `MAIL_FROM` is set; outbox mail is never sent.
OUTBOX_FROM = "no-reply@localhost"
SMTP_TIMEOUT_SECONDS = 10.0

# Plain text, and the code is never in the subject, so a lock-screen preview does not show it.
CODE_SUBJECTS = {
    EmailCodePurpose.VERIFY_EMAIL: "Your sign-up code",
    EmailCodePurpose.PASSWORD_RESET: "Your password reset code",
}
_CODE_BODIES = {
    EmailCodePurpose.VERIFY_EMAIL: """\
Your code to confirm this email address is:

    {code}

It works for {ttl_minutes} minutes. If you did not sign up, ignore this email; without the code no
account can use this address.
""",
    EmailCodePurpose.PASSWORD_RESET: """\
Your code to reset your password is:

    {code}

It works for {ttl_minutes} minutes. If you did not ask for this, ignore this email; your password
stays the same.
""",
}


class MailMessage(BaseModel):
    """One plain-text email to one address."""

    model_config = ConfigDict(frozen=True)

    to: str
    subject: str
    body: str


class MailSender(Protocol):
    """Something that delivers a `MailMessage`. Raises on failure."""

    def send(self, message: MailMessage) -> None: ...


def code_email(purpose: EmailCodePurpose, *, to: str, code: str, ttl_minutes: int) -> MailMessage:
    """The email that gives `to` a code for `purpose`."""
    body = _CODE_BODIES[purpose].format(code=code, ttl_minutes=ttl_minutes)
    return MailMessage(to=to, subject=CODE_SUBJECTS[purpose], body=body)


def _email_message(message: MailMessage, sender: str) -> EmailMessage:
    email = EmailMessage()
    email["From"] = sender
    email["To"] = message.to
    email["Subject"] = message.subject
    email.set_content(message.body)
    return email


class OutboxSender:
    """Writes each message as a `.eml` file into a folder, for local development."""

    def __init__(self, folder: Path, sender: str = OUTBOX_FROM) -> None:
        self.folder = folder
        self.sender = sender

    def send(self, message: MailMessage) -> None:
        """Write `message` to a new file; names sort by time, so the newest is last."""
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.folder / f"{stamp}-{uuid.uuid4().hex[:8]}.eml"
        path.write_bytes(bytes(_email_message(message, self.sender)))
        logger.info("email written to the outbox")


class SmtpSender:
    """Sends through an SMTP server with STARTTLS and a login. Takes up to a few seconds."""

    def __init__(self, *, host: str, port: int, username: str, password: str, sender: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender

    def send(self, message: MailMessage) -> None:
        """Connect, upgrade to TLS, log in and send `message`. Raises on any failure."""
        with smtplib.SMTP(self.host, self.port, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(self.username, self.password)
            server.send_message(_email_message(message, self.sender))
        logger.info("email sent")


def send_quietly(sender: MailSender, message: MailMessage) -> None:
    """Send `message`, logging a failure by its error class only (no address, no link).

    Runs after the answer was sent, so nobody is left to hear the error.
    """
    try:
        sender.send(message)
    except (smtplib.SMTPException, OSError) as error:
        logger.error("email failed: %s", type(error).__name__)


def build_mail_sender(settings: ApiSettings) -> MailSender:
    """The sender `settings` ask for.

    Raises ValueError for `MAIL_BACKEND=outbox` together with `AUTH_COOKIE_SECURE=true`: secure
    cookies mean a deployed app, which must really send.
    """
    if settings.mail_backend == "smtp":
        host, username = settings.mail_smtp_host, settings.mail_smtp_username
        password, sender = settings.mail_smtp_password, settings.mail_from
        # The settings model already refuses smtp without these keys; this only narrows types.
        if host is None or username is None or password is None or sender is None:
            raise ValueError("MAIL_BACKEND=smtp needs every MAIL_SMTP_* key and MAIL_FROM")
        return SmtpSender(
            host=host,
            port=settings.mail_smtp_port,
            username=username,
            password=password.get_secret_value(),
            sender=sender,
        )
    if settings.auth_cookie_secure:
        raise ValueError(
            "MAIL_BACKEND=outbox only works with AUTH_COOKIE_SECURE=false (local development); "
            "set MAIL_BACKEND=smtp"
        )
    return OutboxSender(settings.mail_outbox_dir, settings.mail_from or OUTBOX_FROM)
