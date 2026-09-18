"""Add identity, RBAC, definition registries, jobs, and generic audit fields.

Revision ID: 0002_authority_registries
Revises: 0001_authority
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_authority_registries"
down_revision = "0001_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "jobs"'))

    op.add_column(
        "audit_records",
        sa.Column("entity_type", sa.String(128), nullable=False, server_default="unknown"),
        schema="audit",
    )
    op.add_column(
        "audit_records",
        sa.Column("organization_id", sa.String(128), nullable=False, server_default="unknown"),
        schema="audit",
    )
    op.add_column(
        "audit_records",
        sa.Column("actor_kind", sa.String(16), nullable=False, server_default="HUMAN"),
        schema="audit",
    )
    op.add_column(
        "audit_records",
        sa.Column(
            "reason",
            sa.Text(),
            nullable=False,
            server_default="Authority state changed",
        ),
        schema="audit",
    )
    op.add_column(
        "audit_records",
        sa.Column("event_metadata", sa.JSON(), nullable=False, server_default="{}"),
        schema="audit",
    )

    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="core",
    )
    op.create_table(
        "organizations",
        sa.Column("organization_id", sa.String(128), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(128),
            sa.ForeignKey("core.tenants.tenant_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="core",
    )
    op.create_table(
        "workspaces",
        sa.Column("workspace_id", sa.String(128), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(128),
            sa.ForeignKey("core.tenants.tenant_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "organization_id",
            sa.String(128),
            sa.ForeignKey("core.organizations.organization_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="core",
    )
    op.create_table(
        "actors",
        sa.Column("actor_id", sa.String(128), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(128),
            sa.ForeignKey("core.tenants.tenant_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "organization_id",
            sa.String(128),
            sa.ForeignKey("core.organizations.organization_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="core",
    )
    op.create_table(
        "workspace_memberships",
        sa.Column(
            "actor_id",
            sa.String(128),
            sa.ForeignKey("core.actors.actor_id"),
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("roles", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("permission_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("scope_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("data_scope", sa.String(32), nullable=False, server_default="OWN_ASSIGNED"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="core",
    )
    op.create_table(
        "role_grants",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("organization_id", sa.String(128), primary_key=True),
        sa.Column("role_id", sa.String(128), primary_key=True),
        sa.Column("permission_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("scope_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="core",
    )
    op.create_table(
        "registry_definitions",
        sa.Column("registry_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("subject_type", sa.String(32), nullable=False, index=True),
        sa.Column("subject_id", sa.String(128), nullable=False, index=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("contract_payload", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False, index=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False, index=True),
        sa.Column("decision_id", sa.String(128), nullable=True),
        sa.Column("release_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "subject_type",
            "subject_id",
            "version",
            name="uq_registry_definition_version",
        ),
        schema="core",
    )
    op.create_table(
        "queue",
        sa.Column("job_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("job_type", sa.String(64), nullable=False, index=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("correlation_id", sa.String(128), nullable=False, index=True),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(300), nullable=False),
        sa.Column("locked_by", sa.String(200), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_error_code", sa.String(128), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "job_type", "idempotency_key", name="uq_job_idempotency"
        ),
        schema="jobs",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
