from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from alos.config import Settings
from alos.main import create_app


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL="postgresql+asyncpg://alos:alos@localhost:5432/alos_test",
        GENESIS_BASE_URL="http://genesis.test",
        GENESIS_INTERNAL_TOKEN="test-only-token",  # noqa: S106
        OTEL_SERVICE_NAME="alos-backend-test",
    )


@pytest_asyncio.fixture()
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
