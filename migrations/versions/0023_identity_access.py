"""Normalize role authority and add employee-owned account lifecycle state.

Revision ID: 0023_identity_access
Revises: 0022_strategy_planning
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_identity_access"
down_revision = "0022_strategy_planning"
branch_labels = None
depends_on = None


NON_EQUIVALENT_ROLES = (
    "BUSINESS_REVIEWER",
    "AI_ADMIN",
    "TECHNICAL_REVIEWER",
    "QA_ASSURANCE",
)


def upgrade() -> None:
    connection = op.get_bind()

    multiple_roles = (
        connection.execute(
            sa.text(
                """
            SELECT actor_id, workspace_id, roles::text AS roles
            FROM core.workspace_memberships
            WHERE CASE
                WHEN jsonb_typeof(roles::jsonb) = 'array'
                THEN jsonb_array_length(roles::jsonb) <> 1
                ELSE true
            END
            ORDER BY actor_id, workspace_id
            """
            )
        )
        .mappings()
        .all()
    )
    if multiple_roles:
        details = ", ".join(
            f"actor_id={row['actor_id']} workspace_id={row['workspace_id']} roles={row['roles']}"
            for row in multiple_roles
        )
        raise RuntimeError(f"identity migration requires one role per membership: {details}")

    obsolete_memberships = (
        connection.execute(
            sa.text(
                """
            SELECT actor_id, workspace_id, role, membership.active, membership.revoked_at
            FROM core.workspace_memberships AS membership
            CROSS JOIN LATERAL jsonb_array_elements_text(membership.roles::jsonb) AS legacy(role)
            WHERE legacy.role = ANY(:roles)
            ORDER BY legacy.role, actor_id, workspace_id
            """
            ),
            {"roles": list(NON_EQUIVALENT_ROLES)},
        )
        .mappings()
        .all()
    )
    if obsolete_memberships:
        details = ", ".join(
            f"role={row['role']} actor_id={row['actor_id']} workspace_id={row['workspace_id']} "
            f"active={row['active']} revoked_at={row['revoked_at']}"
            for row in obsolete_memberships
        )
        raise RuntimeError(
            f"identity roles require governed remediation before migration: {details}"
        )

    grant_conflicts = (
        connection.execute(
            sa.text(
                """
            SELECT old_grant.tenant_id, old_grant.organization_id, old_grant.role_id,
                   replacement.role_id AS replacement_role
            FROM core.role_grants AS old_grant
            JOIN core.role_grants AS replacement
              ON replacement.tenant_id = old_grant.tenant_id
             AND replacement.organization_id = old_grant.organization_id
             AND replacement.role_id = CASE old_grant.role_id
                 WHEN 'WORKSPACE_LEAD' THEN 'DIVISION_LEAD'
                 WHEN 'WORKSPACE_MEMBER' THEN 'DIVISION_MEMBER'
             END
            WHERE old_grant.role_id IN ('WORKSPACE_LEAD', 'WORKSPACE_MEMBER')
              AND (old_grant.permission_refs::jsonb
                       IS DISTINCT FROM replacement.permission_refs::jsonb
                   OR old_grant.scope_refs::jsonb IS DISTINCT FROM replacement.scope_refs::jsonb
                   OR old_grant.active IS DISTINCT FROM replacement.active)
            """
            )
        )
        .mappings()
        .all()
    )
    if grant_conflicts:
        details = ", ".join(
            f"tenant_id={row['tenant_id']} organization_id={row['organization_id']} "
            f"role={row['role_id']} conflicts_with={row['replacement_role']}"
            for row in grant_conflicts
        )
        raise RuntimeError(f"identity role grant policy conflicts require remediation: {details}")

    connection.execute(
        sa.text(
            """
            DELETE FROM core.role_grants AS old_grant
            USING core.role_grants AS replacement
            WHERE old_grant.tenant_id = replacement.tenant_id
              AND old_grant.organization_id = replacement.organization_id
              AND old_grant.role_id = CASE replacement.role_id
                  WHEN 'DIVISION_LEAD' THEN 'WORKSPACE_LEAD'
                  WHEN 'DIVISION_MEMBER' THEN 'WORKSPACE_MEMBER'
              END
              AND old_grant.permission_refs::jsonb = replacement.permission_refs::jsonb
              AND old_grant.scope_refs::jsonb = replacement.scope_refs::jsonb
              AND old_grant.active = replacement.active
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE core.role_grants
            SET role_id = CASE role_id
                WHEN 'WORKSPACE_LEAD' THEN 'DIVISION_LEAD'
                WHEN 'WORKSPACE_MEMBER' THEN 'DIVISION_MEMBER'
                ELSE role_id
            END,
                active = CASE
                    WHEN role_id IN ('BUSINESS_REVIEWER', 'AI_ADMIN',
                                     'TECHNICAL_REVIEWER', 'QA_ASSURANCE')
                    THEN false ELSE active
                END
            WHERE role_id IN ('WORKSPACE_LEAD', 'WORKSPACE_MEMBER', 'BUSINESS_REVIEWER',
                              'AI_ADMIN', 'TECHNICAL_REVIEWER', 'QA_ASSURANCE')
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE core.workspace_memberships AS membership
            SET roles = jsonb_build_array(
                CASE membership.roles::jsonb ->> 0
                    WHEN 'WORKSPACE_LEAD' THEN 'DIVISION_LEAD'
                    WHEN 'WORKSPACE_MEMBER' THEN 'DIVISION_MEMBER'
                END
            )::json
            WHERE membership.roles::jsonb ->> 0 IN ('WORKSPACE_LEAD', 'WORKSPACE_MEMBER')
            """
        )
    )

    op.add_column(
        "workspace_memberships",
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )
    connection.execute(
        sa.text(
            """
            UPDATE core.workspace_memberships
            SET effective_at = created_at, updated_at = created_at
            WHERE effective_at IS NULL OR updated_at IS NULL
            """
        )
    )
    op.alter_column("workspace_memberships", "effective_at", nullable=False, schema="core")
    op.alter_column("workspace_memberships", "updated_at", nullable=False, schema="core")
    op.create_check_constraint(
        "ck_workspace_memberships_one_role",
        "workspace_memberships",
        "jsonb_array_length(roles::jsonb) = 1",
        schema="core",
    )

    op.add_column(
        "auth_accounts",
        sa.Column("primary_workspace_id", sa.String(128), nullable=True),
        schema="core",
    )
    op.add_column(
        "auth_accounts",
        sa.Column("administrative_state", sa.String(16), nullable=False, server_default="ENABLED"),
        schema="core",
    )
    op.add_column(
        "auth_accounts",
        sa.Column("activation_state", sa.String(16), nullable=False, server_default="ACTIVATED"),
        schema="core",
    )
    op.add_column(
        "auth_accounts",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )
    connection.execute(
        sa.text(
            """
            UPDATE core.auth_accounts AS account
            SET primary_workspace_id = account.workspace_id
            WHERE account.workspace_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM core.workspace_memberships AS membership
                  WHERE membership.actor_id = account.actor_id
                    AND membership.workspace_id = account.workspace_id
                    AND membership.active IS TRUE
                    AND membership.revoked_at IS NULL
              )
            """
        )
    )
    op.create_foreign_key(
        "fk_auth_accounts_primary_workspace",
        "auth_accounts",
        "workspaces",
        ["primary_workspace_id"],
        ["workspace_id"],
        source_schema="core",
        referent_schema="core",
    )

    op.add_column(
        "auth_sessions",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        schema="core",
    )

    op.create_table(
        "activation_challenges",
        sa.Column("challenge_id", sa.String(128), primary_key=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["core.auth_accounts.account_id"],
            name="fk_activation_challenge_account",
        ),
        schema="core",
    )
    op.create_index(
        "ix_activation_challenges_account_id",
        "activation_challenges",
        ["account_id"],
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("ALOS production migrations are append-only")
