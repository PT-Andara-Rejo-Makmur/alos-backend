"""Retain KPI source bindings and immutable business calculations."""

import sqlalchemy as sa
from alembic import op

revision = "0037_business_metrics"
down_revision = "0036_operational_lifecycles"
branch_labels = None
depends_on = None


def boundary() -> list[sa.Column]:
    return [
        sa.Column(name, sa.String(128), nullable=False)
        for name in ("tenant_id", "organization_id", "workspace_id")
    ]


def upgrade() -> None:
    op.create_table(
        "source_bindings",
        sa.Column("binding_id", sa.String(128), primary_key=True),
        *boundary(),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_id", "target_version"],
            ["strategy.targets.target_id", "strategy.targets.version"],
        ),
        sa.UniqueConstraint("target_id", "target_version", name="uq_target_source_binding"),
        schema="strategy",
    )
    op.create_table(
        "business_calculations",
        sa.Column("calculation_id", sa.String(128), primary_key=True),
        *boundary(),
        sa.Column(
            "binding_id",
            sa.String(128),
            sa.ForeignKey("strategy.source_bindings.binding_id"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("observation_id", sa.String(128), nullable=False),
        sa.Column("evidence_id", sa.String(128), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("binding_id", "request_id", name="uq_business_calculation_request"),
        schema="strategy",
    )
    op.create_index(
        "ix_business_calculation_scope",
        "business_calculations",
        ["tenant_id", "organization_id", "workspace_id"],
        schema="strategy",
    )


def downgrade() -> None:
    raise RuntimeError("Retain source bindings and calculation provenance; use forward correction")
