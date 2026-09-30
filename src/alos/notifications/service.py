"""Notification service coordinating email channels and branded message dispatch."""

from __future__ import annotations

import logging
from collections.abc import Callable

from alos.config import Settings
from alos.notifications.models import DeliveryResult, EmailMessage
from alos.notifications.smtp_adapter import EmailAdapter, InMemoryEmailAdapter, SmtpEmailAdapter
from alos.notifications.templates import (
    render_account_reactivated_email,
    render_account_suspended_email,
    render_activation_email,
    render_password_changed_email,
    render_password_reset_email,
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Enterprise notification intent boundary with configurable delivery channels."""

    def __init__(
        self,
        settings: Settings,
        adapter: EmailAdapter | None = None,
        activation_sink: Callable[[str, str], None] | None = None,
        *,
        email_adapter: EmailAdapter | None = None,
        app_public_url: str | None = None,
    ) -> None:
        self._settings = settings
        effective_adapter = adapter or email_adapter
        if effective_adapter is not None:
            self._adapter = effective_adapter
        elif settings.EMAIL_PROVIDER in {"test", "sink", "memory"}:
            self._adapter = InMemoryEmailAdapter(sink=activation_sink)
        else:
            self._adapter = SmtpEmailAdapter(settings)
        self._from_name = settings.EMAIL_FROM_NAME or "ALOS"
        self._from_email = settings.EMAIL_FROM or "notification@alos.local"
        raw_url = app_public_url or settings.APP_PUBLIC_URL
        self._public_url = raw_url.rstrip("/") if raw_url else "http://localhost:3000"

    async def send_activation_invitation(
        self,
        *,
        recipient: str | None = None,
        to_email: str | None = None,
        employee_name: str = "",
        workspace_name: str = "ALOS",
        role: str | None = None,
        role_name: str | None = None,
        activation_token: str,
    ) -> DeliveryResult:
        target_email = recipient or to_email or ""
        effective_role = role or role_name or "Anggota"
        activation_url = f"{self._public_url}/aktivasi?token={activation_token}"
        subject, text_content, html_content = render_activation_email(
            employee_name=employee_name,
            workspace_name=workspace_name,
            role=effective_role,
            activation_url=activation_url,
        )
        message = EmailMessage(
            to_email=target_email,
            subject=subject,
            text_content=text_content,
            html_content=html_content,
            from_name=self._from_name,
            from_email=self._from_email,
        )
        return await self._adapter.send(message)

    async def send_activation_success(
        self,
        *,
        recipient: str | None = None,
        to_email: str | None = None,
        employee_name: str | None = None,
        display_name: str | None = None,
    ) -> DeliveryResult:
        target_email = recipient or to_email or ""
        name = employee_name or display_name or "Pengguna"
        subject = "Akun ALOS Berhasil Diaktifkan"
        text = f"""Halo {name},

Akun ALOS Anda telah berhasil diaktifkan. Anda kini dapat masuk ke sistem.

Salam,
ALOS
"""
        html = f"""<!DOCTYPE html>
<html lang="id">
<head><meta charset="UTF-8"><title>{subject}</title></head>
<body style="font-family: sans-serif; line-height: 1.6; color: #1e293b; padding: 24px;">
  <div style="max-width: 560px; margin: 0 auto; background: #fff; padding: 24px;
              border: 1px solid #e2e8f0; border-radius: 8px;">
    <h2 style="color: #15803d; margin-top: 0;">Akun ALOS Berhasil Diaktifkan</h2>
    <p>Halo <strong>{name}</strong>,</p>
    <p>Akun ALOS Anda telah berhasil diaktifkan. Anda kini dapat masuk ke sistem.</p>
    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
    <p style="font-size: 12px; color: #64748b;">&copy; ALOS</p>
  </div>
</body>
</html>"""
        message = EmailMessage(
            to_email=target_email,
            subject=subject,
            text_content=text,
            html_content=html,
            from_name=self._from_name,
            from_email=self._from_email,
        )
        return await self._adapter.send(message)

    async def send_password_reset(
        self,
        *,
        recipient: str | None = None,
        to_email: str | None = None,
        display_name: str | None = None,
        employee_name: str | None = None,
        reset_token: str,
        ttl_minutes: int = 60,
    ) -> DeliveryResult:
        target_email = recipient or to_email or ""
        name = display_name or employee_name or "Pengguna"
        reset_url = f"{self._public_url}/atur-ulang-sandi?token={reset_token}"
        subject, text_content, html_content = render_password_reset_email(
            display_name=name,
            reset_url=reset_url,
            ttl_minutes=ttl_minutes,
        )
        message = EmailMessage(
            to_email=target_email,
            subject=subject,
            text_content=text_content,
            html_content=html_content,
            from_name=self._from_name,
            from_email=self._from_email,
        )
        return await self._adapter.send(message)

    async def send_account_suspended(
        self,
        *,
        recipient: str | None = None,
        to_email: str | None = None,
        display_name: str | None = None,
        employee_name: str | None = None,
        reason: str | None = None,
    ) -> DeliveryResult:
        target_email = recipient or to_email or ""
        name = display_name or employee_name or "Pengguna"
        subject, text_content, html_content = render_account_suspended_email(
            display_name=name,
            reason=reason,
        )
        message = EmailMessage(
            to_email=target_email,
            subject=subject,
            text_content=text_content,
            html_content=html_content,
            from_name=self._from_name,
            from_email=self._from_email,
        )
        return await self._adapter.send(message)

    async def send_account_reactivated(
        self,
        *,
        recipient: str | None = None,
        to_email: str | None = None,
        display_name: str | None = None,
        employee_name: str | None = None,
    ) -> DeliveryResult:
        target_email = recipient or to_email or ""
        name = display_name or employee_name or "Pengguna"
        subject, text_content, html_content = render_account_reactivated_email(
            display_name=name,
        )
        message = EmailMessage(
            to_email=target_email,
            subject=subject,
            text_content=text_content,
            html_content=html_content,
            from_name=self._from_name,
            from_email=self._from_email,
        )
        return await self._adapter.send(message)

    async def send_password_changed(
        self,
        *,
        recipient: str | None = None,
        to_email: str | None = None,
        display_name: str | None = None,
        employee_name: str | None = None,
    ) -> DeliveryResult:
        target_email = recipient or to_email or ""
        name = display_name or employee_name or "Pengguna"
        subject, text_content, html_content = render_password_changed_email(
            display_name=name,
        )
        message = EmailMessage(
            to_email=target_email,
            subject=subject,
            text_content=text_content,
            html_content=html_content,
            from_name=self._from_name,
            from_email=self._from_email,
        )
        return await self._adapter.send(message)
