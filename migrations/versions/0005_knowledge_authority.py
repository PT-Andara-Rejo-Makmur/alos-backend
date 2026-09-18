"""Add document, source, and evidence authority records.

Revision ID: 0005_knowledge_authority
Revises: 0004_agent_run_authority
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_knowledge_authority"
down_revision = "0004_agent_run_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "evidence"'))
    op.create_table(
        "documents",
        sa.Column("record_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.String(128), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("data_classification", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("owner_actor_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "workspace_id", "document_id", name="uq_document_scope"),
        schema="core",
    )
    op.create_table(
        "document_versions",
        sa.Column("record_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("document_id", sa.String(128), nullable=False, index=True),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False, index=True),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "document_id",
            "version",
            name="uq_document_version_scope",
        ),
        schema="core",
    )
    op.create_table(
        "sources",
        sa.Column("record_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(128), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("data_classification", sa.String(32), nullable=False),
        sa.Column("document_id", sa.String(128), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "workspace_id", "source_id", name="uq_source_scope"),
        schema="core",
    )
    op.create_table(
        "source_versions",
        sa.Column("record_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("source_id", sa.String(128), nullable=False, index=True),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("verified_by", sa.String(128), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "source_id",
            "source_version",
            name="uq_source_version_scope",
        ),
        schema="core",
    )
    op.create_table(
        "evidence_refs",
        sa.Column("evidence_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("source_id", sa.String(128), nullable=False, index=True),
        sa.Column("source_version", sa.String(100), nullable=True),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("anchor", sa.String(500), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("data_classification", sa.String(32), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False, index=True),
        sa.Column("metadata_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        schema="evidence",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
