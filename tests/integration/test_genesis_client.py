import httpx
import pytest
from pydantic import SecretStr

from alos.integrations.genesis import GenesisClient, GenesisClientError


@pytest.mark.asyncio
async def test_genesis_client_propagates_correlation_and_structures_http_error() -> None:
    seen_correlation: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_correlation
        seen_correlation = request.headers.get("X-Correlation-ID")
        return httpx.Response(503, json={"code": "UNAVAILABLE"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://genesis.test") as http:
        client = GenesisClient(
            base_url="http://genesis.test",
            internal_token=SecretStr("test-token"),
            client=http,
        )
        with pytest.raises(GenesisClientError) as raised:
            await client.health(correlation_id="corr_genesis_001")

    assert seen_correlation == "corr_genesis_001"
    assert raised.value.code == "GENESIS_HTTP_ERROR"
    assert raised.value.status_code == 503
    assert raised.value.retryable


@pytest.mark.asyncio
async def test_genesis_client_structures_invalid_json_response() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"not-json"))
    async with httpx.AsyncClient(transport=transport, base_url="http://genesis.test") as http:
        client = GenesisClient(
            base_url="http://genesis.test",
            internal_token=SecretStr("test-token"),
            client=http,
        )
        with pytest.raises(GenesisClientError) as raised:
            await client.diagnostic(correlation_id="corr_invalid_json_001")

    assert raised.value.code == "GENESIS_INVALID_RESPONSE"
    assert not raised.value.retryable
