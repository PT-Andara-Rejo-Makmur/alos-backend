"""Bind material Shared Work approvals to actions, content and one consumption."""

import sqlalchemy as sa
from alembic import op

revision = "0029_material_approvals"
down_revision = "0028_password_reset_challenges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("requested_action", sa.String(64), nullable=True),
        sa.Column("subject_snapshot", sa.String(64), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by", sa.String(128), nullable=True),
        sa.Column("transition_ref", sa.String(128), nullable=True),
    ):
        op.add_column("work_approvals", column, schema="core")
    op.create_check_constraint(
        "ck_material_approval_snapshot",
        "work_approvals",
        "(requested_action IS NULL AND subject_snapshot IS NULL) OR "
        "(requested_action IS NOT NULL AND subject_snapshot IS NOT NULL)",
        schema="core",
    )
    op.create_check_constraint(
        "ck_material_approval_consumption",
        "work_approvals",
        "(consumed_at IS NULL AND consumed_by IS NULL AND transition_ref IS NULL) OR "
        "(consumed_at IS NOT NULL AND consumed_by IS NOT NULL AND transition_ref IS NOT NULL "
        "AND status = 'APPROVED' AND requested_action IS NOT NULL)",
        schema="core",
    )
    op.create_index(
        "uq_work_approval_transition",
        "work_approvals",
        ["transition_ref"],
        unique=True,
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
