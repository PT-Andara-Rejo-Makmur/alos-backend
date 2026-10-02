"""Real PostgreSQL conversation persistence and every isolation dimension."""

import os
from dataclasses import replace
from uuid import uuid4

import pytest

from alos.ara.orchestration import response
from alos.ara.repository import AraRepository
from alos.identity import Principal
from alos.persistence.database import Database
from alos.security.errors import PlatformError


@pytest.mark.asyncio
async def test_conversation_refresh_and_boundary_isolation() -> None:
    database = Database(
        os.environ.get(
            "ALOS_TEST_DATABASE_URL", "postgresql+asyncpg://alos:alos@127.0.0.1:5432/alos_test"
        )
    )
    repository = AraRepository(database.session_factory)
    principal = Principal(
        actor_id=f"actor_{uuid4().hex}",
        tenant_id="tenant_ara_test",
        organization_id="org_ara_test",
        workspace_id="workspace_ara_test",
    )
    try:
        thread = await repository.create(principal, "Isolation proof")
        thread_id, run_id = thread["thread_id"], f"run_{uuid4().hex}"
        await repository.reserve(principal, thread_id, "question", run_id, "corr_ara_test")
        with pytest.raises(PlatformError) as conflict:
            await repository.reserve(
                principal, thread_id, "racing", f"run_{uuid4().hex}", "corr_other"
            )
        assert conflict.value.status_code == 409
        await repository.finish(
            principal, thread_id, run_id, response("NEEDS_INFO", "Detail diperlukan."), "COMPLETED"
        )
        refreshed = AraRepository(database.session_factory)
        assert (await refreshed.get(principal, thread_id))["actor_id"] == principal.actor_id
        messages = await refreshed.messages(principal, thread_id)
        assert [message["role"] for message in messages] == ["USER", "ASSISTANT"]
        assert (await refreshed.run(principal, thread_id, run_id))["status"] == "COMPLETED"
        for field in ("tenant_id", "organization_id", "workspace_id", "actor_id"):
            foreign = replace(principal, **{field: "foreign_boundary"})
            assert await refreshed.list_threads(foreign) == []
            for read in (refreshed.get, refreshed.messages):
                with pytest.raises(PlatformError) as invisible:
                    await read(foreign, thread_id)
                assert invisible.value.status_code == 404
            with pytest.raises(PlatformError):
                await refreshed.run(foreign, thread_id, run_id)
    finally:
        await database.dispose()
