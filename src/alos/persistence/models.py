"""Minimal authoritative persistence model; cross-repo payloads remain in alos-contracts."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alos.persistence.base import Base


class ReviewPackageRecord(Base):
    __tablename__ = "review_packages"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    subject_id: Mapped[str] = mapped_column(String(128))
    subject_version: Mapped[str] = mapped_column(String(64))
    contract_version: Mapped[str] = mapped_column(String(64))
    evidence_uri: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthoritativeDecisionRecord(Base):
    __tablename__ = "authoritative_decisions"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_id: Mapped[str] = mapped_column(
        ForeignKey("core.review_packages.review_id"), index=True
    )
    authority: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(128))
    rationale: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReleaseRecord(Base):
    __tablename__ = "releases"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    release_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_id: Mapped[str] = mapped_column(
        ForeignKey("core.review_packages.review_id"), index=True
    )
    state: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[str] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditRecord(Base):
    __tablename__ = "audit_records"
    __table_args__ = {"schema": "audit"}  # noqa: RUF012

    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128))
    actor_id: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    outcome: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
