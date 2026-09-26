"""Add cross-domain operational indexes.

Revision ID: 0021_domain_indexes
Revises: 0020_genesis_governance
"""

from alembic import op

revision = "0021_domain_indexes"
down_revision = "0020_genesis_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_sales_site_visits_property_unit",
        "site_visits",
        "property_units",
        ["property_unit_id"],
        ["property_unit_id"],
        source_schema="sales",
        referent_schema="property",
    )
    op.create_foreign_key(
        "fk_sales_bookings_property_unit",
        "bookings",
        "property_units",
        ["property_unit_id"],
        ["property_unit_id"],
        source_schema="sales",
        referent_schema="property",
    )
    op.create_foreign_key(
        "fk_sales_closings_property_unit",
        "closings",
        "property_units",
        ["property_unit_id"],
        ["property_unit_id"],
        source_schema="sales",
        referent_schema="property",
    )
    op.create_foreign_key(
        "fk_sales_pricing_items_property_unit",
        "pricing_items",
        "property_units",
        ["property_unit_id"],
        ["property_unit_id"],
        source_schema="sales",
        referent_schema="property",
    )

    op.create_foreign_key(
        "fk_sales_collaterals_campaign",
        "collaterals",
        "campaigns",
        ["campaign_id"],
        ["campaign_id"],
        source_schema="sales",
        referent_schema="marketing",
    )
    op.create_foreign_key(
        "fk_marketing_attributions_customer",
        "attributions",
        "customers",
        ["customer_id"],
        ["customer_id"],
        source_schema="marketing",
        referent_schema="sales",
    )
    op.create_foreign_key(
        "fk_marketing_attributions_lead",
        "attributions",
        "leads",
        ["lead_id"],
        ["lead_id"],
        source_schema="marketing",
        referent_schema="sales",
    )
    op.create_foreign_key(
        "fk_marketing_attributions_campaign",
        "attributions",
        "campaigns",
        ["campaign_id"],
        ["campaign_id"],
        source_schema="marketing",
        referent_schema="marketing",
    )
    op.create_foreign_key(
        "fk_marketing_attributions_channel",
        "attributions",
        "channels",
        ["channel_id"],
        ["channel_id"],
        source_schema="marketing",
        referent_schema="marketing",
    )

    indexes = [
        ("ix_projects_scope", "core", "projects", ["tenant_id", "organization_id"]),
        ("ix_tasks_scope_status", "core", "tasks", ["tenant_id", "organization_id", "status"]),
        (
            "ix_approvals_scope_status",
            "core",
            "work_approvals",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_reports_scope_status",
            "core",
            "work_reports",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_findings_scope_status",
            "core",
            "work_findings",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_finance_tx_scope_date",
            "finance",
            "bank_transactions",
            ["tenant_id", "organization_id", "transaction_date"],
        ),
        (
            "ix_finance_ar_scope_status",
            "finance",
            "receivables",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_finance_ap_scope_status",
            "finance",
            "payables",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_hr_employee_scope_status",
            "hr",
            "employees",
            ["tenant_id", "organization_id", "employment_status"],
        ),
        (
            "ix_hr_leave_scope_status",
            "hr",
            "leave_requests",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_legal_contract_scope_status",
            "legal",
            "contracts",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_legal_expiry_scope_date",
            "legal",
            "expiries",
            ["tenant_id", "organization_id", "expires_at"],
        ),
        (
            "ix_sales_lead_scope_status",
            "sales",
            "leads",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_sales_booking_scope_status",
            "sales",
            "bookings",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_property_unit_scope_status",
            "property",
            "property_units",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_property_milestone_project",
            "property",
            "project_milestones",
            ["tenant_id", "organization_id", "project_id"],
        ),
        (
            "ix_it_incident_scope_status",
            "it",
            "incidents",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_it_security_scope_status",
            "it",
            "security_findings",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_genesis_uat_scope_status",
            "genesis",
            "uat_gates",
            ["tenant_id", "organization_id", "status"],
        ),
        (
            "ix_genesis_decision_scope_status",
            "genesis",
            "technical_decisions",
            ["tenant_id", "organization_id", "status"],
        ),
    ]
    for name, schema, table, columns in indexes:
        op.create_index(name, table, columns, schema=schema)


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
