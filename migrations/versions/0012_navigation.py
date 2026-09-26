"""Add authoritative workspace navigation catalog.

Revision ID: 0012_navigation
Revises: 0011_default_workspace_bootstrap
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_navigation"
down_revision = "0011_default_workspace_bootstrap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "navigation_groups",
        sa.Column("navigation_group_id", sa.String(128), primary_key=True),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("code", name="uq_navigation_group_code"),
        schema="core",
    )
    op.create_table(
        "navigation_items",
        sa.Column("navigation_item_id", sa.String(128), primary_key=True),
        sa.Column(
            "navigation_group_id",
            sa.String(128),
            sa.ForeignKey("core.navigation_groups.navigation_group_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("route", sa.String(500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("code", name="uq_navigation_item_code"),
        schema="core",
    )
    op.create_table(
        "workspace_navigation",
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "navigation_item_id",
            sa.String(128),
            sa.ForeignKey("core.navigation_items.navigation_item_id"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
