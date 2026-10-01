"""Production SMTP email adapter and in-memory test sink."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import smtplib
import ssl
from collections.abc import Callable
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Protocol

from alos.config import Settings
from alos.notifications.models import DeliveryResult, EmailMessage

logger = logging.getLogger(__name__)


class EmailAdapter(Protocol):
    async def send(self, message: EmailMessage) -> DeliveryResult: ...


class InMemoryEmailAdapter:
    """Deterministic in-memory email sink for unit and integration tests."""

    def __init__(self, sink: Callable[[str, str], None] | None = None) -> None:
        self.sent_messages: list[EmailMessage] = []
        self._sink = sink

    async def send(self, message: EmailMessage) -> DeliveryResult:
        self.sent_messages.append(message)
        if self._sink is not None:
            self._sink(message.to_email, message.subject)
        return DeliveryResult(success=True, recipient=message.to_email)


class SmtpEmailAdapter:
    """Production SMTP transport supporting STARTTLS and secret-safe error handling."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, message: EmailMessage) -> DeliveryResult:
        return await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> DeliveryResult:
        host = self._settings.SMTP_HOST
        port = self._settings.SMTP_PORT
        timeout = self._settings.SMTP_TIMEOUT_SECONDS
        use_tls = self._settings.SMTP_USE_TLS
        username = self._settings.SMTP_USERNAME
        password = self._settings.SMTP_PASSWORD.get_secret_value()

        if not self._settings.is_email_configured or not message.from_email:
            logger.warning("SMTP transport unconfigured; delivery skipped.")
            return DeliveryResult(
                success=False,
                recipient=message.to_email,
                error="SMTP configuration incomplete",
            )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = message.subject
        msg["From"] = email.utils.formataddr((message.from_name, message.from_email))
        msg["To"] = message.to_email
        msg["Date"] = email.utils.formatdate(localtime=True)

        msg.attach(MIMEText(message.text_content, "plain", "utf-8"))
        msg.attach(MIMEText(message.html_content, "html", "utf-8"))

        server: smtplib.SMTP | None = None
        try:
            if use_tls and port == 465:
                server = smtplib.SMTP_SSL(
                    host=host, port=port, timeout=timeout, context=ssl.create_default_context()
                )
            else:
                server = smtplib.SMTP(host=host, port=port, timeout=timeout)
            if use_tls and port != 465:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
            return DeliveryResult(success=True, recipient=message.to_email)
        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP authentication failed for host %s:%d", host, port)
            return DeliveryResult(
                success=False,
                recipient=message.to_email,
                error="SMTP authentication error",
            )
        except (smtplib.SMTPException, OSError) as exc:
            # Secret-safe logging: log type only without raw exception data
            logger.error("SMTP delivery failure to %s: %s", message.to_email, type(exc).__name__)
            return DeliveryResult(
                success=False,
                recipient=message.to_email,
                error=f"SMTP delivery error: {type(exc).__name__}",
            )
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    logger.debug("Failed closing SMTP connection", exc_info=True)
