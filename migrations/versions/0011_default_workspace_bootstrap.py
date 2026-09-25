"""Seed the canonical default organization workspace catalog.

Revision ID: 0011_default_workspace_bootstrap
Revises: 0010_unified_lifecycle

This migration is additive. It does not remove or deactivate historical synthetic
workspace rows because they may already be referenced by membership or audit data.
"""

from alembic import op
from sqlalchemy import text

revision = "0011_default_workspace_bootstrap"
down_revision = "0010_unified_lifecycle"
branch_labels = None
depends_on = None


WORKSPACES = (
    ("workspace_executive", "executive", "Executive Workspace", "EXECUTIVE", "EXECUTIVE"),
    ("workspace_finance", "finance", "Finance Workspace", "BUSINESS", "FINANCE"),
    ("workspace_hr", "hr", "HR & People Workspace", "BUSINESS", "HR"),
    ("workspace_legal", "legal", "Legal & Compliance Workspace", "BUSINESS", "LEGAL"),
    ("workspace_sales", "sales", "Sales & Marketing Workspace", "BUSINESS", "SALES"),
    ("workspace_property", "property", "Property & Project Workspace", "BUSINESS", "PROPERTY"),
    ("workspace_it", "it", "IT & Technology Workspace", "IT_OPERATIONS", "IT"),
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        text(
            """
            INSERT INTO core.tenants (tenant_id, name, active)
            SELECT 'tenant_default', 'tenant_default', true
            WHERE NOT EXISTS (
                SELECT 1 FROM core.tenants WHERE tenant_id = 'tenant_default'
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO core.organizations (organization_id, tenant_id, name, active)
            SELECT 'org_default', 'tenant_default', 'org_default', true
            WHERE NOT EXISTS (
                SELECT 1 FROM core.organizations WHERE organization_id = 'org_default'
            )
            """
        )
    )
    for workspace_id, workspace_key, name, workspace_type, division_code in WORKSPACES:
        connection.execute(
            text(
                """
                INSERT INTO core.workspaces (
                    workspace_id, tenant_id, organization_id, name, workspace_key,
                    workspace_type, organizational_unit_id, division_code, active
                )
                SELECT :workspace_id, :tenant_id, :organization_id, :name,
                       :workspace_key, :workspace_type, NULL, :division_code, true
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM core.workspaces
                    WHERE organization_id = :organization_id
                      AND lower(workspace_key) = lower(:workspace_key)
                )
                """
            ).bindparams(
                workspace_id=workspace_id,
                tenant_id="tenant_default",
                organization_id="org_default",
                name=name,
                workspace_key=workspace_key,
                workspace_type=workspace_type,
                division_code=division_code,
            )
        )


def downgrade() -> None:
    raise RuntimeError("Default workspace bootstrap migration is append-only")
