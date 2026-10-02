"""Conversation persistence is separate from reusable business memory."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alos.persistence.base import Base


class AraThreadRecord(Base):
    __tablename__ = "ara_threads"
    __table_args__ = (
        Index(
            "ix_ara_thread_boundary",
            "tenant_id",
            "organization_id",
            "workspace_id",
            "actor_id",
            "updated_at",
        ),
        {"schema": "core"},
    )
    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    organization_id: Mapped[str] = mapped_column(String(128))
    workspace_id: Mapped[str] = mapped_column(String(128))
    actor_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32))
    active_run_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AraMessageRecord(Base):
    __tablename__ = "ara_messages"
    __table_args__ = {"schema": "core"}  # noqa: RUF012
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("core.ara_threads.thread_id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AraRunRecord(Base):
    __tablename__ = "ara_runs"
    __table_args__ = {"schema": "core"}  # noqa: RUF012
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("core.ara_threads.thread_id"), index=True)
    correlation_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
