"""Minimal authoritative persistence model; cross-repo payloads remain in alos-contracts."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    review_id: Mapped[str] = mapped_column(ForeignKey("core.review_packages.review_id"), index=True)
    authority: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(128))
    rationale: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReleaseRecord(Base):
    __tablename__ = "releases"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    release_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("core.review_packages.review_id"), index=True)
    state: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[str] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tenant_id: Mapped[str] = mapped_column(String(128), default="unknown", index=True)
    organization_id: Mapped[str] = mapped_column(String(128), default="unknown", index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), default="unknown", index=True)
    subject_id: Mapped[str] = mapped_column(String(128), default="unknown", index=True)
    subject_version: Mapped[str] = mapped_column(String(64), default="0.0.0")
    materiality: Mapped[str] = mapped_column(String(32), default="NON_MATERIAL")
    correlation_id: Mapped[str] = mapped_column(String(128), default="unknown", index=True)
    it_decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    director_decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False)
    rollback_target_release_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ever_released: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditRecord(Base):
    __tablename__ = "audit_records"
    __table_args__ = {"schema": "audit"}  # noqa: RUF012

    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(128), default="unknown")
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), default="unknown")
    workspace_id: Mapped[str] = mapped_column(String(128))
    actor_id: Mapped[str] = mapped_column(String(128))
    actor_kind: Mapped[str] = mapped_column(String(16), default="HUMAN")
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    outcome: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="Authority state changed")
    event_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TenantRecord(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OrganizationRecord(Base):
    __tablename__ = "organizations"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    organization_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("core.tenants.tenant_id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("core.tenants.tenant_id"), index=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("core.organizations.organization_id"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ActorRecord(Base):
    __tablename__ = "actors"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("core.tenants.tenant_id"), index=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("core.organizations.organization_id"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkspaceMembershipRecord(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    actor_id: Mapped[str] = mapped_column(ForeignKey("core.actors.actor_id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("core.workspaces.workspace_id"), primary_key=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    permission_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    scope_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    data_scope: Mapped[str] = mapped_column(String(32), default="OWN_ASSIGNED")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RoleGrantRecord(Base):
    __tablename__ = "role_grants"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    permission_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    scope_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RegistryDefinitionRecord(Base):
    __tablename__ = "registry_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "subject_type",
            "subject_id",
            "version",
            name="uq_registry_definition_version",
        ),
        {"schema": "core"},
    )

    registry_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    contract_payload: Mapped[dict[str, object]] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    lifecycle_state: Mapped[str] = mapped_column(String(32), index=True)
    created_by: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    release_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobQueueRecord(Base):
    __tablename__ = "queue"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "job_type",
            "idempotency_key",
            name="uq_job_idempotency",
        ),
        {"schema": "jobs"},
    )

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    owner_actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(300))
    locked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    safe_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ToolDefinitionRecord(Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tool_id", name="uq_tool_definition_tenant"),
        {"schema": "core"},
    )

    tool_definition_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    tool_id: Mapped[str] = mapped_column(String(128), index=True)
    required_permission: Mapped[str] = mapped_column(String(200))
    required_scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    lifecycle_state: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_policy: Mapped[str] = mapped_column(String(32), default="OPTIONAL")
    allowlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    production_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    adapter_key: Mapped[str] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ToolIdempotencyRecord(Base):
    __tablename__ = "tool_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "tool_id",
            "idempotency_key",
            name="uq_tool_idempotency_authority",
        ),
        {"schema": "governance"},
    )

    idempotency_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    tool_id: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_digest: Mapped[str] = mapped_column(String(64))
    output: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentRunRecord(Base):
    """Authoritative lifecycle/reference only; reasoning traces remain in GENESIS."""

    __tablename__ = "agent_runs"
    __table_args__ = {"schema": "ai_runtime"}  # noqa: RUF012

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    root_run_id: Mapped[str] = mapped_column(String(128), index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    actor_id: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_version: Mapped[str] = mapped_column(String(64))
    capability_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    registry_digest: Mapped[str] = mapped_column(String(64))
    lifecycle_authorization: Mapped[str] = mapped_column(String(32))
    authorized_tool_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    request_payload: Mapped[dict[str, object]] = mapped_column(JSON)
    result_payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_state: Mapped[str] = mapped_column(String(32), default="NONE")
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    usage_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cost_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    structured_result_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_budget: Mapped[float | None] = mapped_column(Float, nullable=True)


class AgentRunStepRecord(Base):
    """Step-level persistence for run reconstruction without informal logs."""

    __tablename__ = "agent_run_steps"
    __table_args__ = {"schema": "ai_runtime"}  # noqa: RUF012

    step_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    step_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tool_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    output_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_cost: Mapped[float | None] = mapped_column(Float, nullable=True)


class BacklogCandidateRecord(Base):
    __tablename__ = "backlog_candidates"
    __table_args__ = {"schema": "ai_runtime"}  # noqa: RUF012

    candidate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    finding_id: Mapped[str] = mapped_column(String(128), index=True)
    recommendation_id: Mapped[str] = mapped_column(String(128), index=True)
    impact: Mapped[str] = mapped_column(Text)
    priority_suggestion: Mapped[str] = mapped_column(String(32))
    owner_suggestion: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    approval_state: Mapped[str] = mapped_column(String(32), default="DRAFT")
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    scope_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentAuthorityRecord(Base):
    """Backend-owned document metadata; document reasoning remains in GENESIS."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "document_id", name="uq_document_scope"),
        {"schema": "core"},
    )

    record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(128))
    data_classification: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    owner_actor_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentVersionAuthorityRecord(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "document_id",
            "version",
            name="uq_document_version_scope",
        ),
        {"schema": "core"},
    )

    record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(100))
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    source_version: Mapped[str] = mapped_column(String(100))
    storage_uri: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceAuthorityRecord(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "source_id", name="uq_source_scope"),
        {"schema": "core"},
    )

    record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(32))
    data_classification: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceVersionAuthorityRecord(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "source_id",
            "source_version",
            name="uq_source_version_scope",
        ),
        {"schema": "core"},
    )

    record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    source_version: Mapped[str] = mapped_column(String(100))
    storage_uri: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(32), index=True)
    verified_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceAuthorityRecord(Base):
    """Canonical evidence references, not GENESIS reasoning traces."""

    __tablename__ = "evidence_refs"
    __table_args__ = {"schema": "evidence"}  # noqa: RUF012

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    source_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uri: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71))
    anchor: Mapped[str | None] = mapped_column(String(500), nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_classification: Mapped[str] = mapped_column(String(32))
    validation_status: Mapped[str] = mapped_column(String(32), index=True)
    metadata_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthAccountRecord(Base):
    __tablename__ = "auth_accounts"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = {"schema": "core"}  # noqa: RUF012

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("core.auth_accounts.account_id"),
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True)
    token_hash: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ReleaseLifecycleEventRecord(Base):
    __tablename__ = "release_lifecycle_events"
    __table_args__ = {"schema": "governance"}  # noqa: RUF012

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(ForeignKey("core.releases.release_id"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_state: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    reason: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
