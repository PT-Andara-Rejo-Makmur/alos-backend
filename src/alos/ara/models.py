"""Conversation persistence is separate from reusable business memory."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    runtime_mode: Mapped[str] = mapped_column(
        String(32), default="DETERMINISTIC_TEST", server_default="DETERMINISTIC_TEST"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AraProgressRecord(Base):
    __tablename__ = "ara_progress_events"
    __table_args__ = (
        UniqueConstraint("run_id", "event_key", name="uq_ara_progress_event"),
        CheckConstraint(
            "kind IN ('UNDERSTANDING','RETRIEVING','ANALYZING','PREPARING',"
            "'WAITING_FOR_REVIEW','COMPLETED','FAILED')",
            name="ck_ara_progress_kind",
        ),
        Index("ix_ara_progress_run", "run_id", "event_id"),
        {"schema": "core"},
    )
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("core.ara_runs.run_id"))
    event_key: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
