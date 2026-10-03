"""Extend recorded decisions without duplicating employees or changing domain authority."""

import sqlalchemy as sa
from alembic import op

revision = "0036_operational_lifecycles"
down_revision = "0035_business_relationships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column(
            "ci_run_id", sa.String(128), sa.ForeignKey("it.ci_runs.ci_run_id"), nullable=True
        ),
        sa.Column("deployment_reference", sa.Text(), nullable=True),
        sa.Column("verification_notes", sa.Text(), nullable=True),
    ):
        op.add_column("releases", column, schema="it")
    op.add_column(
        "payables",
        sa.Column("payment_authorized_by", sa.String(128), nullable=True),
        schema="finance",
    )
    op.add_column(
        "payables",
        sa.Column("payment_authorized_at", sa.DateTime(timezone=True), nullable=True),
        schema="finance",
    )
    op.add_column(
        "leave_requests", sa.Column("decision_reason", sa.Text(), nullable=True), schema="hr"
    )
    op.add_column(
        "candidates",
        sa.Column(
            "employee_id", sa.String(128), sa.ForeignKey("hr.employees.employee_id"), nullable=True
        ),
        schema="hr",
    )
    op.create_index(
        "uq_hr_candidate_employee", "candidates", ["employee_id"], unique=True, schema="hr"
    )
    for column in (
        sa.Column(
            "requesting_workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("headcount", sa.Integer(), nullable=True),
    ):
        op.add_column("recruitments", column, schema="hr")
    op.create_check_constraint(
        "ck_recruitment_headcount",
        "recruitments",
        "headcount IS NULL OR headcount > 0",
        schema="hr",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only; retain decision evidence")
