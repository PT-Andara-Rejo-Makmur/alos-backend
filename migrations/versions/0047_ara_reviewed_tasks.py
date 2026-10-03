"""Keep human execution receipts separate from immutable ARA proposals."""

import sqlalchemy as sa
from alembic import op

revision = "0047_ara_reviewed_tasks"
down_revision = "0046_contract_business_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ara_proposal_executions",
        sa.Column("execution_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
        ),
        sa.Column(
            "actor_id", sa.String(128), sa.ForeignKey("core.actors.actor_id"), nullable=False
        ),
        sa.Column("run_id", sa.String(128), sa.ForeignKey("core.ara_runs.run_id"), nullable=False),
        sa.Column("proposal_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(128), sa.ForeignKey("core.tasks.task_id"), nullable=False),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "organization_id",
            "workspace_id",
            "actor_id",
            "proposal_id",
            name="uq_ara_proposal_execution",
        ),
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Retain human review and task execution receipts; use an additive correction."
    )
