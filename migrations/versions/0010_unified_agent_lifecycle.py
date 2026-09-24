"""Enforce one active agent registry version per authoritative subject.

Revision ID: 0010_unified_lifecycle
Revises: 0009_persistent_release
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_unified_lifecycle"
down_revision = "0009_persistent_release"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_registry_one_active_agent_subject",
        "registry_definitions",
        ["tenant_id", "organization_id", "workspace_id", "subject_id"],
        unique=True,
        schema="core",
        postgresql_where=sa.text(
            "subject_type = 'agent' AND lifecycle_state = 'ACTIVE'"
        ),
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
