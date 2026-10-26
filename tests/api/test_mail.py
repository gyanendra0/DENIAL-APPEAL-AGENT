"""Outgoing email: the reset text, the outbox files, the SMTP steps, quiet failures and which
sender the settings build."""

import logging
import smtplib
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from src.api import mail
from src.api.mail import (
    OUTBOX_FROM,
    RESET_SUBJECT,
    MailMessage,
    OutboxSender,
    SmtpSender,
    build_mail_sender,
    reset_email,
    send_quietly,
)
from src.config.settings import ApiSettings

SECRET = "a-made-up-mail-test-secret-of-forty-chars"  # noqa: S105  (made up)
LINK = "https://app.example.test/app/reset-password#token=made.up.token"
MESSAGE = MailMessage(to="ann@x.org", subject="Made up", body="A made-up body.\n")


def _settings(**changes: Any) -> ApiSettings:
    return ApiSettings(  # type: ignore[call-arg]
        auth_token_secret=SecretStr(SECRET), _env_file=None, **changes
    )


def test_the_reset_email_holds_the_link_and_the_lifetime_and_nothing_else() -> None:
    message = reset_email(to="ann@x.org", link=LINK, ttl_minutes=30)
    assert (message.to, message.subject) == ("ann@x.org", RESET_SUBJECT)
    assert LINK in message.body
    assert "within 30 minutes" in message.body
    assert "works once" in message.body
    assert "ann@x.org" not in message.body


def test_the_outbox_writes_one_readable_file_per_message(tmp_path: Path) -> None:
    sender = OutboxSender(tmp_path / "outbox")
    sender.send(MESSAGE)
    sender.send(MESSAGE.model_copy(update={"subject": "Second"}))
    files = sorted((tmp_path / "outbox").glob("*.eml"))
    assert len(files) == 2
    first = message_from_bytes(files[0].read_bytes())
    assert (first["From"], first["To"], first["Subject"]) == (OUTBOX_FROM, "ann@x.org", "Made up")
    assert first.get_payload(decode=True) == b"A made-up body.\n"
    assert message_from_bytes(files[1].read_bytes())["Subject"] == "Second"


class _FakeSmtp:
    """Stands in for `smtplib.SMTP` and records each step."""

    calls: list[tuple[str, Any]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.calls.append(("connect", (host, port, timeout)))

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *_: object) -> None:
        self.calls.append(("quit", None))

    def starttls(self, context: object) -> None:
        self.calls.append(("starttls", context is not None))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", (username, password)))

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append(("send", (message["From"], message["To"], message["Subject"])))


def test_smtp_connects_upgrades_to_tls_logs_in_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSmtp.calls = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    sender = SmtpSender(
        host="smtp.example.test",
        port=587,
        username="made-up-user",
        password="made-up-pass",  # noqa: S106  (made up)
        sender="from@example.test",
    )
    sender.send(MESSAGE)
    assert _FakeSmtp.calls == [
        ("connect", ("smtp.example.test", 587, mail.SMTP_TIMEOUT_SECONDS)),
        ("starttls", True),
        ("login", ("made-up-user", "made-up-pass")),
        ("send", ("from@example.test", "ann@x.org", "Made up")),
        ("quit", None),
    ]


class _BrokenSender:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def send(self, message: MailMessage) -> None:
        raise self.error


@pytest.mark.parametrize(
    "error",
    [smtplib.SMTPAuthenticationError(535, b"made up"), ConnectionRefusedError("made up")],
)
def test_a_failed_send_is_logged_by_its_class_without_the_address(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    send_quietly(_BrokenSender(error), reset_email(to="ann@x.org", link=LINK, ttl_minutes=30))
    assert f"email failed: {type(error).__name__}" in caplog.text
    assert "ann@x.org" not in caplog.text
    assert "made.up.token" not in caplog.text


def test_another_error_is_not_swallowed() -> None:
    with pytest.raises(RuntimeError):
        send_quietly(_BrokenSender(RuntimeError("a bug")), MESSAGE)


def test_settings_for_smtp_build_an_smtp_sender() -> None:
    sender = build_mail_sender(
        _settings(
            mail_backend="smtp",
            mail_smtp_host="smtp.example.test",
            mail_smtp_port=2525,
            mail_smtp_username="made-up-user",
            mail_smtp_password=SecretStr("made-up-pass"),
            mail_from="from@example.test",
        )
    )
    assert isinstance(sender, SmtpSender)
    assert (sender.host, sender.port, sender.username, sender.password, sender.sender) == (
        "smtp.example.test",
        2525,
        "made-up-user",
        "made-up-pass",
        "from@example.test",
    )


def test_smtp_settings_missing_a_key_are_refused_even_when_built_without_checks() -> None:
    settings = _settings().model_copy(update={"mail_backend": "smtp"})
    with pytest.raises(ValueError, match="MAIL_BACKEND=smtp"):
        build_mail_sender(settings)


def test_the_outbox_is_built_only_without_secure_cookies(tmp_path: Path) -> None:
    sender = build_mail_sender(
        _settings(auth_cookie_secure=False, mail_outbox_dir=tmp_path, mail_from="me@x.org")
    )
    assert isinstance(sender, OutboxSender)
    assert (sender.folder, sender.sender) == (tmp_path, "me@x.org")
    with pytest.raises(ValueError, match="AUTH_COOKIE_SECURE=false"):
        build_mail_sender(_settings(auth_cookie_secure=True))
