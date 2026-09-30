"""Notification domain data contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to_email: str
    subject: str
    text_content: str
    html_content: str
    from_name: str
    from_email: str


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    success: bool
    recipient: str
    error: str | None = None
