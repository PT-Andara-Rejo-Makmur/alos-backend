"""Create minimal authoritative schemas and records.

Revision ID: 0001_authority
Revises: None
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_authority"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for schema in ("core", "audit", "ai_runtime", "research"):
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    op.create_table(
        "review_packages",
        sa.Column("review_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False, index=True),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("subject_version", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("evidence_uri", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_table(
        "authoritative_decisions",
        sa.Column("decision_id", sa.String(128), primary_key=True),
        sa.Column(
            "review_id",
            sa.String(128),
            sa.ForeignKey("core.review_packages.review_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("authority", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_table(
        "releases",
        sa.Column("release_id", sa.String(128), primary_key=True),
        sa.Column(
            "review_id",
            sa.String(128),
            sa.ForeignKey("core.review_packages.review_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("decided_by", sa.String(128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_table(
        "audit_records",
        sa.Column("audit_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(128), nullable=False, index=True),
        sa.Column("entity_id", sa.String(128), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False, index=True),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        schema="audit",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
