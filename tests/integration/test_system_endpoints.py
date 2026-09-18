import httpx
import pytest

from alos.config import Settings
from alos.main import create_app


@pytest.mark.asyncio
async def test_health_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Correlation-ID": "corr_health_001"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Correlation-ID"] == "corr_health_001"


@pytest.mark.asyncio
async def test_readiness_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database_configured": True,
        "genesis_configured": True,
    }


@pytest.mark.asyncio
async def test_configured_web_origin_can_read_public_backend(client: httpx.AsyncClient) -> None:
    origin = "http://127.0.0.1:3000"
    response = await client.get("/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-expose-headers"] == "X-Correlation-ID"


@pytest.mark.asyncio
async def test_system_info_declares_backend_authority(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/system/info")
    assert response.status_code == 200
    assert response.json()["authority"] == "ALOS_BACKEND"


@pytest.mark.asyncio
async def test_internal_namespace_is_deny_by_default(client: httpx.AsyncClient) -> None:
    response = await client.get("/internal/v1/health")
    assert response.status_code == 401
    assert response.json()["code"] == "INTERNAL_AUTH_DENIED"


@pytest.mark.asyncio
async def test_application_lifespan_starts_and_stops(settings: Settings) -> None:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert app.state.started is True
    assert app.state.started is False
