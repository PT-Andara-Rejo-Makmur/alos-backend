"""Real PostgreSQL migration regression for the authoritative persistence schema."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from alos.persistence import models  # noqa: F401
from alos.persistence.base import Base
from alos.persistence.strategy_models import StrategyBase


def _database_url(name: str) -> str:
    source = os.environ.get(
        "ALOS_TEST_DATABASE_URL",
        "postgresql+asyncpg://alos:alos@127.0.0.1:5432/alos_test",
    )
    parts = urlsplit(source)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _asyncpg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _recreate_database(name: str) -> None:
    admin = await asyncpg.connect(_asyncpg_url(_database_url("postgres")))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


def _upgrade(url: str, revision: str) -> None:
    environment = {**os.environ, "DATABASE_URL": url}
    subprocess.run(  # noqa: S603 - executable and revision are repository-controlled
        [sys.executable, "-m", "alembic", "upgrade", revision],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )


def _attempt_upgrade(url: str, revision: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - arguments are fixed test migrations and an explicit test database URL
        [sys.executable, "-m", "alembic", "upgrade", revision],
        check=False,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_name", "starting_revision"),
    (
        ("alos_migration_fresh", None),
        ("alos_migration_incremental", "0006_auth_accounts"),
        ("alos_migration_release", "0009_persistent_release"),
        ("alos_migration_existing", "0010_unified_lifecycle"),
        ("alos_migration_strategy", "0021_domain_indexes"),
    ),
)
async def test_postgres_upgrade_matches_runtime_metadata(
    database_name: str, starting_revision: str | None
) -> None:
    await _recreate_database(database_name)
    url = _database_url(database_name)
    if starting_revision is not None:
        _upgrade(url, starting_revision)
    _upgrade(url, "head")

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            (
                actual,
                strategy_tables,
                strategy_indexes,
                relationship_constraints,
            ) = await connection.run_sync(
                lambda sync: (
                    {
                        (table.schema, table.name): set(
                            column["name"]
                            for column in inspect(sync).get_columns(table.name, schema=table.schema)
                        )
                        for table in (
                            *Base.metadata.sorted_tables,
                            *StrategyBase.metadata.sorted_tables,
                        )
                    },
                    set(inspect(sync).get_table_names(schema="strategy")),
                    {
                        index["name"]
                        for table in inspect(sync).get_table_names(schema="strategy")
                        for index in inspect(sync).get_indexes(table, schema="strategy")
                    },
                    {
                        item["name"]
                        for item in inspect(sync).get_unique_constraints(
                            "target_relationships", schema="strategy"
                        )
                    },
                )
            )
    finally:
        await engine.dispose()

    expected = {
        (table.schema, table.name): {column.name for column in table.columns}
        for table in (*Base.metadata.sorted_tables, *StrategyBase.metadata.sorted_tables)
    }
    assert actual == expected
    assert strategy_tables == {
        "plans",
        "objectives",
        "targets",
        "target_observations",
        "target_relationships",
        "planning_assumptions",
        "cascade_rules",
        "cascade_runs",
        "cascade_results",
        "planning_constraints",
        "constraint_results",
        "kpi_definitions",
        "initiatives",
        "target_revisions",
        "source_bindings",
        "business_calculations",
    }
    assert strategy_indexes >= {
        "ix_strategy_plans_scope_state",
        "ix_strategy_objectives_plan",
        "ix_strategy_targets_plan_state",
        "ix_strategy_observations_target",
        "ix_strategy_assumptions_scope",
        "ix_strategy_relationship_parent",
        "ix_strategy_relationship_child",
        "ix_strategy_cascade_status",
        "ix_strategy_revisions_target",
    }
    assert "uq_strategy_target_relationship_exact_versions" in relationship_constraints


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_name", "roles", "active", "revoked", "message"),
    (
        (
            "alos_role_multi_preflight",
            '["DIVISION_LEAD", "IT_ADMIN"]',
            True,
            False,
            "one role per membership",
        ),
        (
            "alos_role_review_preflight",
            '["BUSINESS_REVIEWER"]',
            True,
            False,
            "role=BUSINESS_REVIEWER actor_id=actor_preflight workspace_id=workspace_it",
        ),
        (
            "alos_role_revoked_review_preflight",
            '["BUSINESS_REVIEWER"]',
            True,
            True,
            "role=BUSINESS_REVIEWER actor_id=actor_preflight workspace_id=workspace_it",
        ),
        (
            "alos_role_inactive_ai_preflight",
            '["AI_ADMIN"]',
            False,
            False,
            "role=AI_ADMIN actor_id=actor_preflight workspace_id=workspace_it",
        ),
        (
            "alos_role_empty_preflight",
            "[]",
            False,
            True,
            "one role per membership",
        ),
    ),
)
async def test_identity_migration_fails_closed_for_unremediated_memberships(
    database_name: str, roles: str, active: bool, revoked: bool, message: str
) -> None:
    await _recreate_database(database_name)
    url = _database_url(database_name)
    _upgrade(url, "0022_strategy_planning")
    connection = await asyncpg.connect(_asyncpg_url(url))
    try:
        await connection.execute(
            """
            INSERT INTO core.actors (actor_id, tenant_id, organization_id, display_name, active)
            VALUES ('actor_preflight', 'tenant_default', 'org_default', 'Preflight Actor', true)
            """
        )
        await connection.execute(
            """
            INSERT INTO core.workspace_memberships (
                actor_id, workspace_id, tenant_id, organization_id, roles,
                permission_refs, scope_refs, data_scope, active, created_at, revoked_at
            )
            VALUES (
                'actor_preflight', 'workspace_it', 'tenant_default', 'org_default',
                $1::json, '["it.read"]'::json, '[]'::json, 'WORKSPACE', $2, now(),
                CASE WHEN $3 THEN now() ELSE NULL END
            )
            """,
            roles,
            active,
            revoked,
        )
    finally:
        await connection.close()

    result = _attempt_upgrade(url, "head")
    assert result.returncode != 0
    assert message in result.stderr
    connection = await asyncpg.connect(_asyncpg_url(url))
    try:
        query = (
            "SELECT roles::text FROM core.workspace_memberships WHERE actor_id='actor_preflight'"
        )
        assert json.loads(await connection.fetchval(query)) == json.loads(roles)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_identity_migration_maps_equivalent_roles_without_changing_authority() -> None:
    database_name = "alos_role_equivalent_migration"
    await _recreate_database(database_name)
    url = _database_url(database_name)
    _upgrade(url, "0022_strategy_planning")
    connection = await asyncpg.connect(_asyncpg_url(url))
    try:
        await connection.execute(
            """
            INSERT INTO core.actors (actor_id, tenant_id, organization_id, display_name, active)
            VALUES ('actor_equivalent', 'tenant_default', 'org_default', 'Equivalent Actor', true)
            """
        )
        await connection.execute(
            """
            INSERT INTO core.workspace_memberships (
                actor_id, workspace_id, tenant_id, organization_id, roles,
                permission_refs, scope_refs, data_scope, active, created_at, revoked_at
            )
            VALUES (
                'actor_equivalent', 'workspace_it', 'tenant_default', 'org_default',
                '["WORKSPACE_LEAD"]'::json, '["it.read"]'::json,
                '["scope.it"]'::json, 'WORKSPACE', true, now(), NULL
            )
            """
        )
        await connection.execute(
            """
            INSERT INTO core.workspace_memberships (
                actor_id, workspace_id, tenant_id, organization_id, roles,
                permission_refs, scope_refs, data_scope, active, created_at, revoked_at
            ) VALUES (
                'actor_equivalent', 'workspace_finance', 'tenant_default', 'org_default',
                '["WORKSPACE_MEMBER"]'::json, '["finance.read"]'::json,
                '["scope.finance"]'::json, 'WORKSPACE', false, now(), now()
            )
            """
        )
        await connection.execute(
            """
            INSERT INTO core.role_grants (
                tenant_id, organization_id, role_id, permission_refs, scope_refs, active
            ) VALUES (
                'tenant_default', 'org_default', 'WORKSPACE_LEAD',
                '["strategy.division.manage"]'::json, '["scope.division"]'::json, true
            )
            """
        )
    finally:
        await connection.close()

    _upgrade(url, "head")
    connection = await asyncpg.connect(_asyncpg_url(url))
    try:
        membership = await connection.fetchrow(
            """
            SELECT roles::text AS roles, permission_refs::text AS permissions,
                   scope_refs::text AS scopes, data_scope, active, revoked_at,
                   effective_at, updated_at
            FROM core.workspace_memberships
            WHERE actor_id = 'actor_equivalent' AND workspace_id = 'workspace_it'
            """
        )
        grant = await connection.fetchrow(
            """
            SELECT role_id, permission_refs::text AS permissions, scope_refs::text AS scopes, active
            FROM core.role_grants
            WHERE tenant_id = 'tenant_default' AND organization_id = 'org_default'
            """
        )
        memberships = await connection.fetch(
            "SELECT roles::jsonb AS roles FROM core.workspace_memberships"
        )
    finally:
        await connection.close()

    assert membership["roles"] == '["DIVISION_LEAD"]'
    assert [
        json.loads(row["roles"])
        for row in memberships
        if json.loads(row["roles"]) == ["DIVISION_MEMBER"]
    ]
    assert all(len(json.loads(row["roles"])) == 1 for row in memberships)
    assert membership["permissions"] == '["it.read"]'
    assert membership["scopes"] == '["scope.it"]'
    assert membership["data_scope"] == "WORKSPACE"
    assert membership["active"] is True
    assert membership["revoked_at"] is None
    assert membership["effective_at"] is not None
    assert membership["updated_at"] is not None
    assert grant["role_id"] == "DIVISION_LEAD"
    assert grant["permissions"] == '["strategy.division.manage"]'
    assert grant["scopes"] == '["scope.division"]'
    assert grant["active"] is True
