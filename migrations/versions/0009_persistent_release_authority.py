"""Persist transactional release authority state.

Revision ID: 0009_persistent_release
Revises: 0008_canonical_identity_access
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0009_persistent_release"
down_revision = "0008_canonical_identity_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = datetime.now(UTC)
    op.drop_constraint("releases_review_id_fkey", "releases", schema="core", type_="foreignkey")
    additions = (
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=True),
        sa.Column("assurance_actor_id", sa.String(128), nullable=True),
        sa.Column("it_actor_id", sa.String(128), nullable=True),
        sa.Column("director_actor_id", sa.String(128), nullable=True),
    )
    for column in additions:
        op.add_column("releases", column, schema="core")
    op.execute(
        sa.text(
            "UPDATE core.releases SET created_by = decided_by, created_at = decided_at, "
            "updated_at = decided_at, state_version = 1"
        )
    )
    for name in ("created_by", "created_at", "updated_at", "state_version"):
        op.alter_column("releases", name, nullable=False, schema="core")
    op.create_index(
        "uq_releases_active_subject",
        "releases",
        ["tenant_id", "workspace_id", "subject_id"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("state = 'ACTIVE'"),
    )
    op.create_index(
        "uq_releases_it_decision",
        "releases",
        ["it_decision_id"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("it_decision_id IS NOT NULL"),
    )
    op.create_index(
        "uq_releases_director_decision",
        "releases",
        ["director_decision_id"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("director_decision_id IS NOT NULL"),
    )
    op.execute(
        sa.text("UPDATE core.releases SET updated_at = :now WHERE updated_at IS NULL").bindparams(
            now=now
        )
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
