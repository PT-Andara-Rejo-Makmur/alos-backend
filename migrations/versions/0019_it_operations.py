"""Add IT and Technology operations persistence.

Revision ID: 0019_it_operations
Revises: 0018_property
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_it_operations"
down_revision = "0018_property"
branch_labels = None
depends_on = None


def _scope(schema="core"):
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            index=True,
        ),
    ]


def _dates():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "it"'))

    op.create_table(
        "systems",
        *_scope(),
        sa.Column("system_id", sa.String(128), primary_key=True),
        sa.Column("system_code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("criticality", sa.String(32), nullable=False, server_default="MEDIUM"),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "system_code", name="uq_it_system_code"
        ),
        schema="it",
    )
    op.create_table(
        "integrations",
        *_scope(),
        sa.Column("integration_id", sa.String(128), primary_key=True),
        sa.Column(
            "system_id",
            sa.String(128),
            sa.ForeignKey("it.systems.system_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("integration_type", sa.String(128), nullable=False),
        sa.Column("endpoint_ref", sa.String(500), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "databases",
        *_scope(),
        sa.Column("database_id", sa.String(128), primary_key=True),
        sa.Column(
            "system_id",
            sa.String(128),
            sa.ForeignKey("it.systems.system_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("engine", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(64), nullable=True),
        sa.Column("classification", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "environments",
        *_scope(),
        sa.Column("environment_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("environment_type", sa.String(64), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "repositories",
        *_scope(),
        sa.Column("repository_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default="GITHUB"),
        sa.Column("external_url", sa.String(1000), nullable=True),
        sa.Column("default_branch", sa.String(200), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "cicd_pipelines",
        *_scope(),
        sa.Column("pipeline_id", sa.String(128), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(128),
            sa.ForeignKey("it.repositories.repository_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "ci_runs",
        *_scope(),
        sa.Column("ci_run_id", sa.String(128), primary_key=True),
        sa.Column(
            "pipeline_id",
            sa.String(128),
            sa.ForeignKey("it.cicd_pipelines.pipeline_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("external_run_id", sa.String(200), nullable=True),
        sa.Column("commit_sha", sa.String(200), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "releases",
        *_scope(),
        sa.Column("it_release_id", sa.String(128), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(128),
            sa.ForeignKey("it.repositories.repository_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column(
            "environment_id",
            sa.String(128),
            sa.ForeignKey("it.environments.environment_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PLANNED"),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "technical_debts",
        *_scope(),
        sa.Column("technical_debt_id", sa.String(128), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(32), nullable=False, server_default="MEDIUM"),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "service_monitors",
        *_scope(),
        sa.Column("service_monitor_id", sa.String(128), primary_key=True),
        sa.Column(
            "system_id",
            sa.String(128),
            sa.ForeignKey("it.systems.system_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("check_type", sa.String(128), nullable=False),
        sa.Column("target", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="UNKNOWN"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "incidents",
        *_scope(),
        sa.Column("incident_id", sa.String(128), primary_key=True),
        sa.Column(
            "system_id",
            sa.String(128),
            sa.ForeignKey("it.systems.system_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "security_findings",
        *_scope(),
        sa.Column("security_finding_id", sa.String(128), primary_key=True),
        sa.Column(
            "system_id",
            sa.String(128),
            sa.ForeignKey("it.systems.system_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "backup_policies",
        *_scope(),
        sa.Column("backup_policy_id", sa.String(128), primary_key=True),
        sa.Column(
            "system_id",
            sa.String(128),
            sa.ForeignKey("it.systems.system_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("frequency", sa.String(64), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "backup_runs",
        *_scope(),
        sa.Column("backup_run_id", sa.String(128), primary_key=True),
        sa.Column(
            "backup_policy_id",
            sa.String(128),
            sa.ForeignKey("it.backup_policies.backup_policy_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("artifact_ref", sa.String(1000), nullable=True),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "restore_tests",
        *_scope(),
        sa.Column("restore_test_id", sa.String(128), primary_key=True),
        sa.Column(
            "backup_run_id",
            sa.String(128),
            sa.ForeignKey("it.backup_runs.backup_run_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(32), nullable=False, index=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_dates(),
        schema="it",
    )
    op.create_table(
        "dr_plans",
        *_scope(),
        sa.Column("dr_plan_id", sa.String(128), primary_key=True),
        sa.Column(
            "system_id",
            sa.String(128),
            sa.ForeignKey("it.systems.system_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("rto_minutes", sa.Integer(), nullable=True),
        sa.Column("rpo_minutes", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        *_dates(),
        schema="it",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
