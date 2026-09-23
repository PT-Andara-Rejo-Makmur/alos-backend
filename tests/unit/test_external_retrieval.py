from __future__ import annotations

import httpx
import pytest

from alos.audit import InMemoryAuditRepository
from alos.integrations import (
    ExternalRetrievalError,
    ExternalRetrievalPolicy,
    ExternalRetrievalService,
)

SCOPE = frozenset({"scope.sources.external_read"})


def policy(**changes: object) -> ExternalRetrievalPolicy:
    values: dict[str, object] = {
        "allowed_protocols": frozenset({"https"}),
        "allowed_domains": frozenset({"example.com"}),
        "max_response_bytes": 100,
        "allowed_content_types": frozenset({"application/json"}),
    }
    values.update(changes)
    return ExternalRetrievalPolicy(**values)


def context() -> dict[str, str]:
    return {
        "tenant_id": "tenant_external",
        "organization_id": "org_external",
        "workspace_id": "workspace_external",
        "actor_id": "actor_external",
        "correlation_id": "corr_external_001",
    }


@pytest.mark.asyncio
async def test_external_retrieval_allows_policy_compliant_response_and_audits() -> None:
    audit = InMemoryAuditRepository()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ok":true}',
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        service = ExternalRetrievalService(policy=policy(), audit=audit, client=client)
        result = await service.retrieve(
            "https://example.com/data.json", **context(), scope_refs=SCOPE
        )

    assert result.content == b'{"ok":true}'
    assert [event.outcome for event in audit.list_events(tenant_id="tenant_external")] == [
        "SUCCESS",
        "ALLOWED",
    ]


@pytest.mark.asyncio
async def test_external_retrieval_timeout_is_structured_and_audited() -> None:
    audit = InMemoryAuditRepository()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = ExternalRetrievalService(policy=policy(), audit=audit, client=client)
        with pytest.raises(ExternalRetrievalError) as raised:
            await service.retrieve(
                "https://example.com/data.json?token=secret",
                **context(),
                scope_refs=SCOPE,
            )

    assert raised.value.code == "EGRESS_TIMEOUT"
    events = audit.list_events(tenant_id="tenant_external")
    assert events[0].outcome == "TIMEOUT"
    assert all("secret" not in str(event.metadata) for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://example.com/data", "EGRESS_PROTOCOL_BLOCKED"),
        ("https://not-allowed.test/data", "EGRESS_DOMAIN_BLOCKED"),
        ("file:///etc/passwd", "EGRESS_PROTOCOL_BLOCKED"),
        ("https://127.0.0.1/data", "EGRESS_PRIVATE_NETWORK_BLOCKED"),
    ],
)
async def test_external_retrieval_blocks_unsafe_urls(url: str, code: str) -> None:
    audit = InMemoryAuditRepository()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as client:
        service = ExternalRetrievalService(policy=policy(), audit=audit, client=client)
        with pytest.raises(ExternalRetrievalError) as raised:
            await service.retrieve(url, **context(), scope_refs=SCOPE)
    assert raised.value.code == code

    events = audit.list_events(tenant_id="tenant_external")
    assert events[0].outcome == "BLOCKED"
    assert events[0].metadata["code"] == code


@pytest.mark.asyncio
async def test_external_retrieval_blocks_unauthorized_scope_before_network() -> None:
    audit = InMemoryAuditRepository()
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = ExternalRetrievalService(policy=policy(), audit=audit, client=client)
        with pytest.raises(ExternalRetrievalError) as raised:
            await service.retrieve(
                "https://example.com/data.json", **context(), scope_refs=frozenset()
            )

    assert not called
    assert raised.value.code == "EGRESS_SCOPE_DENIED"
    assert (
        audit.list_events(tenant_id="tenant_external")[0].metadata["code"] == "EGRESS_SCOPE_DENIED"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,content",
    [
        ({"content-type": "text/html"}, b"{}"),
        ({"content-type": "application/json", "content-length": "101"}, b"{}"),
        ({"content-type": "application/json"}, b"x" * 101),
    ],
)
async def test_external_retrieval_enforces_content_type_and_size(
    headers: dict[str, str], content: bytes
) -> None:
    audit = InMemoryAuditRepository()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers=headers, content=content, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        service = ExternalRetrievalService(policy=policy(), audit=audit, client=client)
        with pytest.raises(ExternalRetrievalError) as raised:
            await service.retrieve("https://example.com/data.json", **context(), scope_refs=SCOPE)

    assert raised.value.code in {"EGRESS_CONTENT_TYPE_BLOCKED", "EGRESS_RESPONSE_TOO_LARGE"}
    assert audit.list_events(tenant_id="tenant_external")[0].outcome == "BLOCKED"


@pytest.mark.asyncio
async def test_external_retrieval_rejects_private_dns_resolution() -> None:
    audit = InMemoryAuditRepository()

    async def resolver(_: str) -> set[str]:
        return {"10.0.0.8"}

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as client:
        service = ExternalRetrievalService(
            policy=policy(), audit=audit, client=client, resolver=resolver
        )
        with pytest.raises(ExternalRetrievalError) as raised:
            await service.retrieve("https://example.com/data.json", **context(), scope_refs=SCOPE)

    assert raised.value.code == "EGRESS_PRIVATE_NETWORK_BLOCKED"
    assert audit.list_events(tenant_id="tenant_external")[0].outcome == "BLOCKED"
