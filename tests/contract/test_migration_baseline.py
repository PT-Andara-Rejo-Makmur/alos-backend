import importlib.util
from pathlib import Path

from alos.persistence import models  # noqa: F401
from alos.persistence.base import Base


def test_initial_migration_is_importable_and_append_only() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0001_authoritative_foundation.py"
    )
    spec = importlib.util.spec_from_file_location("alos_initial_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0001_authority"
    assert migration.down_revision is None


def test_authority_registry_migration_is_append_only() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0002_authority_registries.py"
    )
    spec = importlib.util.spec_from_file_location("alos_authority_registry_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0002_authority_registries"
    assert migration.down_revision == "0001_authority"


def test_authoritative_tables_have_explicit_schema_ownership() -> None:
    owned = {(table.schema, table.name) for table in Base.metadata.sorted_tables}
    assert ("core", "review_packages") in owned
    assert ("core", "authoritative_decisions") in owned
    assert ("core", "releases") in owned
    assert ("audit", "audit_records") in owned
    assert ("core", "tenants") in owned
    assert ("core", "organizations") in owned
    assert ("core", "workspaces") in owned
    assert ("core", "actors") in owned
    assert ("core", "workspace_memberships") in owned
    assert ("core", "role_grants") in owned
    assert ("core", "registry_definitions") in owned
    assert ("jobs", "queue") in owned
    assert ("core", "tool_definitions") in owned
    assert ("governance", "tool_idempotency") in owned
    assert ("governance", "release_lifecycle_events") in owned
    assert ("ai_runtime", "agent_runs") in owned
    assert ("ai_runtime", "agent_run_steps") in owned
    assert ("ai_runtime", "backlog_candidates") in owned
    assert ("research", "research_findings") in owned
    assert ("research", "research_recommendations") in owned
    assert ("core", "documents") in owned
    assert ("core", "document_versions") in owned
    assert ("core", "sources") in owned
    assert ("core", "source_versions") in owned
    assert ("evidence", "evidence_refs") in owned


def test_tool_governance_release_migration_is_append_only() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0003_tool_governance_release.py"
    )
    spec = importlib.util.spec_from_file_location("alos_tool_governance_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0003_tool_governance_release"
    assert migration.down_revision == "0002_authority_registries"


def test_agent_run_authority_migration_is_append_only() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0004_agent_run_authority.py"
    )
    spec = importlib.util.spec_from_file_location("alos_agent_run_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0004_agent_run_authority"
    assert migration.down_revision == "0003_tool_governance_release"


def test_knowledge_authority_migration_is_append_only() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0005_knowledge_authority.py"
    )
    spec = importlib.util.spec_from_file_location("alos_knowledge_authority_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0005_knowledge_authority"
    assert migration.down_revision == "0004_agent_run_authority"


def test_auth_account_migration_is_append_only() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0006_auth_accounts.py"
    spec = importlib.util.spec_from_file_location("alos_auth_account_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0006_auth_accounts"
    assert migration.down_revision == "0005_knowledge_authority"


def test_runtime_research_migration_is_append_only() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0007_runtime_research.py"
    )
    spec = importlib.util.spec_from_file_location("alos_runtime_research_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0007_runtime_research"
    assert migration.down_revision == "0006_auth_accounts"

