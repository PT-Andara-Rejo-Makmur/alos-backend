"""Fail-closed external retrieval policy and audited HTTP boundary."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from alos.audit import AuditEvent, AuditSink


class ExternalRetrievalError(ValueError):
    """Safe, policy-level external retrieval failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ExternalRetrievalPolicy:
    allowed_protocols: frozenset[str] = frozenset({"https"})
    allowed_domains: frozenset[str] = frozenset()
    timeout_seconds: float = 10.0
    max_response_bytes: int = 1_000_000
    allowed_content_types: frozenset[str] = frozenset(
        {"application/json", "text/plain", "text/html", "application/xml"}
    )
    block_private_networks: bool = True

    def validate_url(self, url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if scheme not in self.allowed_protocols:
            raise ExternalRetrievalError("EGRESS_PROTOCOL_BLOCKED", "URL protocol is not allowed.")
        if not hostname or parsed.username or parsed.password:
            raise ExternalRetrievalError(
                "EGRESS_URL_INVALID", "URL is invalid for external retrieval."
            )
        if self.block_private_networks and _is_ip_literal(hostname):
            self._reject_private_hostname(hostname)
        if self.allowed_domains and not self._domain_allowed(hostname):
            raise ExternalRetrievalError("EGRESS_DOMAIN_BLOCKED", "URL domain is not allowlisted.")
        if self.block_private_networks:
            self._reject_private_hostname(hostname)
        return scheme, hostname

    def _domain_allowed(self, hostname: str) -> bool:
        return any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in self.allowed_domains
        )

    @staticmethod
    def _reject_private_hostname(hostname: str) -> None:
        try:
            addresses = {ipaddress.ip_address(hostname)}
        except ValueError:
            try:
                addresses = {
                    ipaddress.ip_address(result[4][0])
                    for result in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
                }
            except socket.gaierror:
                return
        if not addresses or any(
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        for address in addresses
        ):
            raise ExternalRetrievalError(
                "EGRESS_PRIVATE_NETWORK_BLOCKED",
                "URL resolves to a private or non-routable network.",
            )


@dataclass(frozen=True, slots=True)
class ExternalRetrievalResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    content: bytes


class ExternalRetrievalService:
    """Retrieve allowlisted public content with bounded size and audit evidence."""

    def __init__(
        self,
        *,
        policy: ExternalRetrievalPolicy,
        audit: AuditSink,
        client: httpx.AsyncClient | None = None,
        resolver: Callable[[str], Awaitable[set[str]]] | None = None,
    ) -> None:
        self._policy = policy
        self._audit = audit
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(policy.timeout_seconds),
        )
        self._owns_client = client is None
        self._resolver = resolver

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def retrieve(
        self,
        url: str,
        *,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        actor_id: str,
        correlation_id: str,
        scope_refs: frozenset[str],
    ) -> ExternalRetrievalResult:
        entity_id = _audit_id(url, correlation_id)
        safe_url = _safe_url(url)
        try:
            if "scope.sources.external_read" not in scope_refs:
                raise ExternalRetrievalError(
                    "EGRESS_SCOPE_DENIED",
                    "The principal is not authorized for external retrieval.",
                )
            _scheme, hostname = self._policy.validate_url(url)
            if self._resolver is not None and self._policy.block_private_networks:
                addresses = await self._resolver(hostname)
                self._reject_addresses(addresses)
            await self._audit_event(
                entity_id,
                tenant_id,
                organization_id,
                workspace_id,
                actor_id,
                correlation_id,
                "external.retrieval.allowed",
                "ALLOWED",
                {"url": safe_url, "host": hostname},
            )
            async with self._client.stream("GET", url) as response:
                if response.is_redirect:
                    raise ExternalRetrievalError(
                        "EGRESS_REDIRECT_BLOCKED", "External redirects are not allowed."
                    )
                if response.status_code >= 400:
                    raise ExternalRetrievalError(
                        "EGRESS_UPSTREAM_ERROR", "External source returned an error response."
                    )
                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if content_type not in self._policy.allowed_content_types:
                    raise ExternalRetrievalError(
                        "EGRESS_CONTENT_TYPE_BLOCKED", "External content type is not allowed."
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise ExternalRetrievalError(
                            "EGRESS_CONTENT_LENGTH_INVALID",
                            "External response content length is invalid.",
                        ) from exc
                    if declared_size > self._policy.max_response_bytes:
                        raise ExternalRetrievalError(
                            "EGRESS_RESPONSE_TOO_LARGE",
                            "External response exceeds the size limit.",
                        )
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > self._policy.max_response_bytes:
                        raise ExternalRetrievalError(
                            "EGRESS_RESPONSE_TOO_LARGE",
                            "External response exceeds the size limit.",
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
            result = ExternalRetrievalResult(
                url=url,
                final_url=str(response.url),
                status_code=response.status_code,
                content_type=content_type,
                content=content,
            )
            await self._audit_event(
                entity_id,
                tenant_id,
                organization_id,
                workspace_id,
                actor_id,
                correlation_id,
                "external.retrieval.completed",
                "SUCCESS",
                {"url": safe_url, "status_code": response.status_code, "bytes": len(content)},
            )
            return result
        except ExternalRetrievalError as exc:
            await self._audit_event(
                entity_id,
                tenant_id,
                organization_id,
                workspace_id,
                actor_id,
                correlation_id,
                "external.retrieval.blocked",
                "BLOCKED",
                {"url": safe_url, "code": exc.code},
            )
            raise
        except httpx.TimeoutException as exc:
            await self._audit_event(
                entity_id, tenant_id, organization_id, workspace_id, actor_id, correlation_id,
                "external.retrieval.failed", "TIMEOUT", {"url": safe_url, "code": "EGRESS_TIMEOUT"},
            )
            raise ExternalRetrievalError("EGRESS_TIMEOUT", "External retrieval timed out.") from exc
        except httpx.RequestError as exc:
            await self._audit_event(
                entity_id, tenant_id, organization_id, workspace_id, actor_id, correlation_id,
                "external.retrieval.failed",
                "FAILED",
                {"url": safe_url, "code": "EGRESS_UNAVAILABLE"},
            )
            raise ExternalRetrievalError(
                "EGRESS_UNAVAILABLE", "External retrieval failed safely."
            ) from exc

    @staticmethod
    def _reject_addresses(addresses: set[str]) -> None:
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise ExternalRetrievalError(
                    "EGRESS_DNS_FAILED", "Resolved address is invalid."
                ) from exc
            if (
                address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified
            ):
                raise ExternalRetrievalError(
                    "EGRESS_PRIVATE_NETWORK_BLOCKED",
                    "URL resolves to a private or non-routable network.",
                )

    async def _audit_event(
        self, entity_id: str, tenant_id: str, organization_id: str, workspace_id: str,
        actor_id: str, correlation_id: str, event_type: str, outcome: str,
        metadata: dict[str, Any],
    ) -> None:
        await self._audit.append(
            AuditEvent(
                event_type=event_type,
                entity_type="external_retrieval",
                entity_id=entity_id,
                tenant_id=tenant_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                correlation_id=correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
                reason="Governed external retrieval policy",
                metadata=metadata,
            )
        )


def _audit_id(url: str, correlation_id: str) -> str:
    digest = hashlib.sha256(f"{url}:{correlation_id}".encode()).hexdigest()[:24]
    return f"retrieval_{digest}"


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
