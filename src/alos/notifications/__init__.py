"""Notification intent and delivery boundary."""

from __future__ import annotations

from alos.notifications.models import DeliveryResult, EmailMessage
from alos.notifications.service import NotificationService
from alos.notifications.smtp_adapter import EmailAdapter, InMemoryEmailAdapter, SmtpEmailAdapter

__all__ = [
    "DeliveryResult",
    "EmailAdapter",
    "EmailMessage",
    "InMemoryEmailAdapter",
    "NotificationService",
    "SmtpEmailAdapter",
]
