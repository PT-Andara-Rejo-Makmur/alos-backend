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


def test_authoritative_tables_have_explicit_schema_ownership() -> None:
    owned = {(table.schema, table.name) for table in Base.metadata.sorted_tables}
    assert ("core", "review_packages") in owned
    assert ("core", "authoritative_decisions") in owned
    assert ("core", "releases") in owned
    assert ("audit", "audit_records") in owned
