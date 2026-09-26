"""Add HR and People workspace domain persistence.

Revision ID: 0015_hr
Revises: 0014_finance
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_hr"
down_revision = "0014_finance"
branch_labels = None
depends_on = None


def _scope(schema="core"):
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("organization_id", sa.String(128), nullable=False, index=True),
        sa.Column(
            "workspace_id",
            sa.String(128),
            sa.ForeignKey("core.workspaces.workspace_id"),
            nullable=False,
            index=True,
        ),
    ]


def _dates():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "hr"'))

    op.create_table(
        "employees",
        *_scope(),
        sa.Column("employee_id", sa.String(128), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=True, index=True),
        sa.Column("employee_number", sa.String(128), nullable=False),
        sa.Column("full_name", sa.String(300), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column(
            "employment_status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"
        ),
        sa.Column("join_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("department_code", sa.String(128), nullable=True, index=True),
        sa.Column("position_title", sa.String(200), nullable=True),
        *_dates(),
        sa.UniqueConstraint(
            "tenant_id", "organization_id", "employee_number", name="uq_hr_employee_number"
        ),
        schema="hr",
    )
    op.create_table(
        "attendances",
        *_scope(),
        sa.Column("attendance_id", sa.String(128), primary_key=True),
        sa.Column(
            "employee_id",
            sa.String(128),
            sa.ForeignKey("hr.employees.employee_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("attendance_date", sa.Date(), nullable=False, index=True),
        sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PRESENT"),
        sa.Column("source", sa.String(64), nullable=True),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "leave_requests",
        *_scope(),
        sa.Column("leave_request_id", sa.String(128), primary_key=True),
        sa.Column(
            "employee_id",
            sa.String(128),
            sa.ForeignKey("hr.employees.employee_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("leave_type", sa.String(64), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PENDING"),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "recruitments",
        *_scope(),
        sa.Column("recruitment_id", sa.String(128), primary_key=True),
        sa.Column("position_title", sa.String(200), nullable=False),
        sa.Column("department_code", sa.String(128), nullable=True),
        sa.Column("employment_type", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "candidates",
        *_scope(),
        sa.Column("candidate_id", sa.String(128), primary_key=True),
        sa.Column(
            "recruitment_id",
            sa.String(128),
            sa.ForeignKey("hr.recruitments.recruitment_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("full_name", sa.String(300), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("source", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="APPLIED"),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "interviews",
        *_scope(),
        sa.Column("interview_id", sa.String(128), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.String(128),
            sa.ForeignKey("hr.candidates.candidate_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("interviewer_actor_id", sa.String(128), nullable=True, index=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="SCHEDULED"),
        sa.Column("notes", sa.Text(), nullable=True),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "onboardings",
        *_scope(),
        sa.Column("onboarding_id", sa.String(128), primary_key=True),
        sa.Column(
            "employee_id",
            sa.String(128),
            sa.ForeignKey("hr.employees.employee_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("target_completion_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("owner_actor_id", sa.String(128), nullable=True),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "performance_reviews",
        *_scope(),
        sa.Column("performance_review_id", sa.String(128), primary_key=True),
        sa.Column(
            "employee_id",
            sa.String(128),
            sa.ForeignKey("hr.employees.employee_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("review_period", sa.String(64), nullable=False),
        sa.Column("reviewer_actor_id", sa.String(128), nullable=True, index=True),
        sa.Column("rating", sa.Numeric(8, 2), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="DRAFT"),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "trainings",
        *_scope(),
        sa.Column("training_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("provider", sa.String(200), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="PLANNED"),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "training_enrollments",
        *_scope(),
        sa.Column("training_enrollment_id", sa.String(128), primary_key=True),
        sa.Column(
            "training_id",
            sa.String(128),
            sa.ForeignKey("hr.trainings.training_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "employee_id",
            sa.String(128),
            sa.ForeignKey("hr.employees.employee_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ENROLLED"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_dates(),
        sa.UniqueConstraint("training_id", "employee_id", name="uq_hr_training_employee"),
        schema="hr",
    )
    op.create_table(
        "successions",
        *_scope(),
        sa.Column("succession_id", sa.String(128), primary_key=True),
        sa.Column("position_title", sa.String(200), nullable=False),
        sa.Column("department_code", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "succession_candidates",
        *_scope(),
        sa.Column("succession_candidate_id", sa.String(128), primary_key=True),
        sa.Column(
            "succession_id",
            sa.String(128),
            sa.ForeignKey("hr.successions.succession_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "employee_id",
            sa.String(128),
            sa.ForeignKey("hr.employees.employee_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("readiness", sa.String(32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_dates(),
        sa.UniqueConstraint("succession_id", "employee_id", name="uq_hr_succession_employee"),
        schema="hr",
    )
    op.create_table(
        "grievances",
        *_scope(),
        sa.Column("grievance_id", sa.String(128), primary_key=True),
        sa.Column(
            "employee_id",
            sa.String(128),
            sa.ForeignKey("hr.employees.employee_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="OPEN"),
        sa.Column("assigned_to", sa.String(128), nullable=True),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "employment_contracts",
        *_scope(),
        sa.Column("employment_contract_id", sa.String(128), primary_key=True),
        sa.Column(
            "employee_id",
            sa.String(128),
            sa.ForeignKey("hr.employees.employee_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("contract_number", sa.String(200), nullable=False),
        sa.Column("contract_type", sa.String(64), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="ACTIVE"),
        sa.Column("document_id", sa.String(128), nullable=True, index=True),
        *_dates(),
        schema="hr",
    )
    op.create_table(
        "personnel_files",
        *_scope(),
        sa.Column("personnel_file_id", sa.String(128), primary_key=True),
        sa.Column(
            "employee_id",
            sa.String(128),
            sa.ForeignKey("hr.employees.employee_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("document_id", sa.String(128), nullable=True, index=True),
        sa.Column("file_type", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        *_dates(),
        schema="hr",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
