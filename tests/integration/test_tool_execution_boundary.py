import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from alos.config import Settings
from alos.dependencies import get_tool_registry
from alos.main import create_app
from alos.tools.registry import ToolRegistration, ToolRegistry

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"
INTERNAL_HEADERS = {"Authorization": "Bearer test-only-token"}


def tool_request() -> dict[str, Any]:
    return {
        "tool_call_id": "toolcall_diagnostic_001",
        "run_id": "run_diagnostic_001",
        "tool_id": "diagnostic.echo",
        "execution_context": {
            "tenant_id": "tenant_diagnostic_001",
            "organization_id": "org_diagnostic_001",
            "workspace_id": "workspace_diagnostic_001",
            "actor_id": "actor_diagnostic_001",
            "authority_context": {
                "role": "diagnostic_runner",
                "authority_level": "SYSTEM",
            },
            "permission_refs": ["tools.diagnostic.execute"],
            "scope_refs": ["scope.diagnostic"],
            "data_classification": "INTERNAL",
            "correlation_id": "corr_tool_boundary_001",
        },
        "arguments": {"message": "hello from GENESIS"},
        "requested_at": "2026-09-17T08:00:00Z",
    }


def make_settings(*, enable_test_tools: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL="postgresql+asyncpg://alos:alos@localhost:5432/alos_test",
        GENESIS_BASE_URL="http://genesis.test",
        GENESIS_INTERNAL_TOKEN="test-only-token",  # noqa: S106
        ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
        ENABLE_TEST_TOOLS=enable_test_tools,
    )


async def post_tool(
    payload: dict[str, Any],
    *,
    enable_test_tools: bool = True,
    registry: ToolRegistry | None = None,
) -> tuple[httpx.Response, Any]:
    app = create_app(make_settings(enable_test_tools=enable_test_tools))
    if registry is not None:
        app.dependency_overrides[get_tool_registry] = lambda: registry
    headers = {
        **INTERNAL_HEADERS,
        "X-Correlation-ID": str(payload["execution_context"]["correlation_id"]),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as client:
        response = await client.post(
            "/internal/v1/tool-requests",
            headers=headers,
            json=payload,
        )
    return response, app


def assert_outcome(response: httpx.Response, status: str, code: str | None = None) -> None:
    assert response.status_code == 200
    document = response.json()
    assert document["status"] == status
    assert document["correlation_id"] == "corr_tool_boundary_001"
    assert response.headers["X-Correlation-ID"] == "corr_tool_boundary_001"
    if code is not None:
        assert document["error"]["code"] == code
        assert document["error"]["correlation_id"] == "corr_tool_boundary_001"


@pytest.mark.asyncio
async def test_valid_tool_request_returns_success_and_audit() -> None:
    response, app = await post_tool(tool_request())

    assert_outcome(response, "SUCCESS")
    assert response.json()["output"]["echo"] == {"message": "hello from GENESIS"}
    assert [record.outcome for record in app.state.tool_audit_sink.records] == [
        "REQUESTED",
        "SUCCESS",
    ]


@pytest.mark.asyncio
async def test_unknown_tool_returns_denied() -> None:
    payload = tool_request()
    payload["tool_id"] = "diagnostic.unknown"
    response, _app = await post_tool(payload)
    assert_outcome(response, "DENIED", "TOOL_UNKNOWN")


@pytest.mark.asyncio
async def test_known_tool_not_in_allowlist_returns_denied() -> None:
    response, _app = await post_tool(tool_request(), enable_test_tools=False)
    assert_outcome(response, "DENIED", "TOOL_NOT_ALLOWED")


@pytest.mark.asyncio
async def test_missing_permission_returns_denied() -> None:
    payload = tool_request()
    payload["execution_context"]["permission_refs"] = []
    response, _app = await post_tool(payload)
    assert_outcome(response, "DENIED", "PERMISSION_DENIED")


@pytest.mark.asyncio
async def test_invalid_scope_returns_denied() -> None:
    payload = tool_request()
    payload["execution_context"]["scope_refs"] = ["scope.other"]
    response, _app = await post_tool(payload)
    assert_outcome(response, "DENIED", "SCOPE_DENIED")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "organization_id", "workspace_id", "actor_id"])
async def test_invalid_authority_context_returns_denied(field: str) -> None:
    payload = tool_request()
    payload["execution_context"][field] = f"invalid_{field}"
    response, _app = await post_tool(payload)
    assert_outcome(response, "DENIED", "AUTHORIZATION_DENIED")


@pytest.mark.asyncio
async def test_invalid_input_schema_returns_rejected() -> None:
    payload = tool_request()
    payload["arguments"] = {"message": 42}
    response, app = await post_tool(payload)

    assert_outcome(response, "REJECTED", "TOOL_INPUT_INVALID")
    assert [record.outcome for record in app.state.tool_audit_sink.records] == [
        "REQUESTED",
        "REJECTED",
    ]


@pytest.mark.asyncio
async def test_malformed_tool_request_is_rejected_by_canonical_contract() -> None:
    payload = tool_request()
    payload.pop("tool_call_id")
    response, app = await post_tool(payload)

    assert response.status_code == 422
    assert response.json()["code"] == "TOOL_REQUEST_INVALID"
    assert app.state.tool_audit_sink.records == []


class FailingAdapter:
    def validate_arguments(self, _arguments: Mapping[str, Any]) -> None:
        return None

    async def execute(
        self,
        _arguments: Mapping[str, Any],
        *,
        execution_context: Mapping[str, Any],
    ) -> Any:
        del execution_context
        raise RuntimeError("deterministic test failure")


class WaitingAdapter:
    def validate_arguments(self, _arguments: Mapping[str, Any]) -> None:
        return None

    async def execute(
        self,
        _arguments: Mapping[str, Any],
        *,
        execution_context: Mapping[str, Any],
    ) -> Any:
        del execution_context
        await asyncio.Event().wait()


def registry_with(adapter: Any, *, timeout_seconds: float = 1.0) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolRegistration(
            tool_id="diagnostic.echo",
            required_permission="tools.diagnostic.execute",
            required_scopes=frozenset({"scope.diagnostic"}),
            adapter=adapter,
            production_enabled=False,
            timeout_seconds=timeout_seconds,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_tool_execution_failure_returns_structured_failed_result() -> None:
    response, app = await post_tool(tool_request(), registry=registry_with(FailingAdapter()))

    assert_outcome(response, "FAILED", "TOOL_EXECUTION_FAILED")
    assert [record.outcome for record in app.state.tool_audit_sink.records] == [
        "REQUESTED",
        "FAILED",
    ]


@pytest.mark.asyncio
async def test_tool_timeout_returns_structured_timeout_result() -> None:
    response, app = await post_tool(
        tool_request(), registry=registry_with(WaitingAdapter(), timeout_seconds=0.001)
    )

    assert_outcome(response, "TIMEOUT", "TOOL_TIMEOUT")
    assert response.json()["error"]["retryable"] is True
    assert [record.outcome for record in app.state.tool_audit_sink.records] == [
        "REQUESTED",
        "TIMEOUT",
    ]


@pytest.mark.asyncio
async def test_correlation_mismatch_returns_denied_and_preserves_body_id() -> None:
    payload = tool_request()
    app = create_app(make_settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as client:
        response = await client.post(
            "/internal/v1/tool-requests",
            headers={**INTERNAL_HEADERS, "X-Correlation-ID": "corr_transport_other_001"},
            json=payload,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "DENIED"
    assert response.json()["error"]["code"] == "CORRELATION_MISMATCH"
    assert response.json()["correlation_id"] == "corr_tool_boundary_001"
