"""Persist scoped document links and task/document checklists.

Revision ID: 0026_shared_work_links
Revises: 0025_shared_work_details
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_shared_work_links"
down_revision = "0025_shared_work_details"
branch_labels = None
depends_on = None


def _scope() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "shared_work_document_links",
        sa.Column("link_id", sa.String(128), primary_key=True),
        *_scope(),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("linked_by", sa.String(128), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "target_type IN ('TASK', 'APPROVAL')",
            name="ck_shared_work_document_link_target",
        ),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "workspace_id", "document_id",
            "target_type", "target_id",
            name="uq_shared_work_document_link",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "document_id"],
            [
                "core.documents.tenant_id", "core.documents.workspace_id",
                "core.documents.document_id",
            ],
        ),
        schema="core",
    )
    op.create_index(
        "ix_shared_work_document_link_target", "shared_work_document_links",
        ["tenant_id", "organization_id", "workspace_id", "target_type", "target_id"], schema="core",
    )
    op.create_table(
        "shared_work_checklist_items",
        sa.Column("item_id", sa.String(128), primary_key=True),
        *_scope(),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("completed_by", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "entity_type IN ('TASK', 'DOCUMENT')", name="ck_shared_work_checklist_entity",
        ),
        schema="core",
    )
    op.create_index(
        "ix_shared_work_checklist_entity", "shared_work_checklist_items",
        ["tenant_id", "organization_id", "workspace_id", "entity_type", "entity_id"], schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
