"""Real PostgreSQL migration regression for the authoritative persistence schema."""

from __future__ import annotations

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_name", "starting_revision"),
    (("alos_migration_fresh", None), ("alos_migration_incremental", "0006_auth_accounts")),
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
            actual = await connection.run_sync(
                lambda sync: {
                    (table.schema, table.name): set(
                        column["name"]
                        for column in inspect(sync).get_columns(table.name, schema=table.schema)
                    )
                    for table in Base.metadata.sorted_tables
                }
            )
    finally:
        await engine.dispose()

    expected = {
        (table.schema, table.name): {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }
    assert actual == expected
