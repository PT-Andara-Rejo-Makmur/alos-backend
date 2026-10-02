"""Principal -> ContextBundle -> existing run authority -> GENESIS -> persisted answer."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from alos.agents.lifecycle import AgentRunAuthority, RunAuthorityError
from alos.ara.delegation import read_child
from alos.ara.repository import AraRepository
from alos.ara.review import review_proposal
from alos.audit import AuditEvent, AuditSink
from alos.context.bundle import ContextBuildRequest, ContextBundleBuilder
from alos.context.policy import ARA_BUDGET, ARA_CAPABILITIES, maximum_classification
from alos.contracts import CanonicalContractCatalog
from alos.evidence import resolve_registry_result
from alos.factory import FactoryOrchestrator
from alos.identity import Principal
from alos.integrations.genesis import GenesisClient
from alos.integrations.genesis.client import GenesisClientError
from alos.memory import MemoryService
from alos.registry import RegistryEntry, RegistryState
from alos.security.errors import PlatformError
from alos.tools.business.catalog import BUSINESS_TOOLS
from alos.tools.registry import ToolRegistry

ARA_SCHEMA = "https://schemas.alos.dev/v1/ara/"
LIMITATIONS = [
    "Mode deterministik terbatas pada skenario bisnis yang terdaftar.",
    "Production Model Provider: Belum Terhubung.",
]


def response(kind: str, answer: str) -> dict[str, Any]:
    return {
        "response_type": kind,
        "answer": answer,
        "sources": [],
        "failed_sources": [],
        "limitations": LIMITATIONS,
    }


def needed_tools(message: str) -> tuple[str, ...]:
    text = message.casefold()
    if any(
        word in text
        for word in (
            "buat task",
            "buat tugas",
            "tindak lanjut",
            "bayar vendor",
            "approve budget",
            "jual unit",
            "aktifkan pricing",
            "buat agent",
        )
    ):
        return ()
    if "kondisi perusahaan" in text:
        return (
            "executive.overview.read",
            "strategy.target.read",
            "sales.overview.read",
            "marketing.overview.read",
            "property.overview.read",
            "finance.overview.read",
            "hr.overview.read",
            "legal.overview.read",
            "it.overview.read",
        )
    for words, tools in (
        (("target penjualan", "target sales"), ("strategy.target.read",)),
        (("lead",), ("sales.lead.list",)),
        (("opportunity", "peluang"), ("sales.opportunity.list",)),
        (("booking", "pesanan"), ("sales.booking.read",)),
        (("task", "tugas"), ("shared.task.list",)),
        (("dokumen", "document"), ("shared.document.read",)),
        (("laporan", "report"), ("shared.report.read",)),
        (("kampanye", "campaign"), ("marketing.campaign.list",)),
        (("unit properti", "unit property"), ("property.unit.list",)),
        (("kemajuan", "progress"), ("property.progress.read",)),
        (("rekrutmen", "recruitment"), ("hr.recruitment.summary",)),
        (("daftar karyawan", "employee"), ("hr.employee.summary",)),
        (("kontrak", "contract"), ("legal.contract.read",)),
        (("daftar sistem", "system"), ("it.system.list",)),
        (("rencana strategis", "renstra", "rkap"), ("strategy.plan.read",)),
        (("hutang", "utang", "payable"), ("finance.payable.list",)),
        (("anggaran", "budget"), ("finance.budget.read",)),
        (("proyek",), ("shared.project.list",)),
        (("persetujuan", "approval"), ("shared.approval.list",)),
        (("piutang",), ("finance.receivable.list",)),
        (("risiko legal", "risk legal"), ("legal.risk.list",)),
        (("incident", "insiden"), ("it.incident.list",)),
        (("finance", "keuangan"), ("finance.overview.read",)),
        (("sales", "penjualan"), ("sales.overview.read",)),
        (("marketing", "pemasaran"), ("marketing.overview.read",)),
        (("property", "properti"), ("property.overview.read",)),
        (("hr", "karyawan"), ("hr.overview.read",)),
        (("legal",), ("legal.overview.read",)),
        (("finding", "temuan"), ("shared.finding.list",)),
    ):
        if any(word in text for word in words):
            return tools
    return ()


class AraOrchestrator:
    def __init__(
        self,
        *,
        repository: AraRepository,
        contracts: CanonicalContractCatalog,
        authority: AgentRunAuthority,
        genesis: GenesisClient,
        registry: ToolRegistry,
        audit: AuditSink,
        test_enabled: bool,
        memory: MemoryService | None = None,
        evidence_registry: Any = None,
        factory: FactoryOrchestrator | None = None,
    ) -> None:
        self.repository, self.contracts = repository, contracts
        self.authority, self.genesis, self.registry = authority, genesis, registry
        self.audit, self.test_enabled = audit, test_enabled
        self.memory = memory
        self.evidence_registry = evidence_registry
        self.factory = factory

    def validate(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.contracts.validate(ARA_SCHEMA + name + ".schema.json", payload)

    async def conversation_memory(self, principal: Principal, thread_id: str) -> list[Any]:
        if self.memory is None or self.evidence_registry is None:
            return []
        admitted = []
        for record in self.memory.retrieve_conversation(principal=principal, thread_id=thread_id):
            if record.run_id is None:
                continue
            try:
                evidence = await resolve_registry_result(
                    self.evidence_registry.get(record.evidence_ref)
                )
                origin = await self.authority.get(record.run_id)
            except (LookupError, RunAuthorityError):
                continue
            if all(
                evidence.get(key) == getattr(principal, key)
                for key in ("tenant_id", "organization_id", "workspace_id")
            ) and (
                evidence.get("source_id") == record.source_ref
                and evidence.get("evidence_id") == record.evidence_ref
                and evidence.get("run_id") == record.run_id
                and evidence.get("correlation_id") == record.correlation_id
                and evidence.get("validation_status") == "VALID"
                and evidence.get("instruction_authority") is False
                and evidence.get("data_classification") == record.classification
                and origin.actor_id == principal.actor_id
                and all(
                    getattr(origin, key) == getattr(principal, key)
                    for key in ("tenant_id", "organization_id", "workspace_id")
                )
                and origin.status.value == "COMPLETED"
                and origin.request.get("input", {}).get("thread_id") == thread_id
            ):
                admitted.append(record)
        return admitted

    def authority_projection(self, principal: Principal) -> dict[str, Any]:
        context = ContextBundleBuilder(self.registry).build(
            principal,
            request=ContextBuildRequest(
                capability_ids=tuple(ARA_CAPABILITIES), tool_ids=tuple(BUSINESS_TOOLS)
            ),
            correlation_id="ara_authority",
        )
        return self.validate(
            "ara-authority-projection",
            {
                **{
                    key: getattr(principal, key)
                    for key in ("tenant_id", "organization_id", "workspace_id", "actor_id")
                },
                "status": context.status,
                "maximum_data_classification": maximum_classification(principal),
                "scope_refs": list(context.scope_refs),
                "allowed_tool_ids": list(context.allowed_tools),
                "allowed_capability_ids": list(context.allowed_capabilities),
                "execution_budget": ARA_BUDGET,
                "runtime_mode": "DETERMINISTIC_TEST",
                "production_provider_connected": False,
            },
        )

    async def record(
        self, principal: Principal, entity_id: str, event: str, correlation_id: str, outcome: str
    ) -> None:
        await self.audit.append(
            AuditEvent(
                event_type=event,
                entity_type="ara",
                entity_id=entity_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
            )
        )

    async def send(
        self, principal: Principal, thread_id: str, payload: dict[str, Any], correlation_id: str
    ) -> dict[str, Any]:
        data = self.validate("ara-message-request", payload)
        await self.repository.get(principal, thread_id)
        run_id = f"run_{uuid4().hex}"
        message = data["message"].strip()
        if not message:
            raise PlatformError("ARA_INPUT_INVALID", "Pesan tidak boleh kosong.", status_code=422)
        await self.repository.reserve(principal, thread_id, message, run_id, correlation_id)
        await self.record(principal, run_id, "ara.run_requested", correlation_id, "REQUESTED")
        memories = await self.conversation_memory(principal, thread_id)
        requested = needed_tools(message)
        reference = data.get("business_reference")
        if reference and reference["domain"] == "SHARED_WORK":
            requested = tuple(
                {
                    "shared.project.list": "shared.project.read",
                    "shared.task.list": "shared.task.read",
                }.get(tool, tool)
                for tool in requested
            )
        if not requested and any(
            word in message.casefold() for word in ("lanjutkan", "sumber sebelumnya")
        ):
            # Governed memory can select a domain, but every fact is read again from its owner.
            domains = [record.metadata.get("business_domain") for record in memories]
            for domain in domains:
                if isinstance(domain, str) and domain in {
                    "sales",
                    "marketing",
                    "property",
                    "finance",
                    "hr",
                    "legal",
                    "it",
                }:
                    requested = needed_tools(str(domain))
                    break
        context = ContextBundleBuilder(self.registry).build(
            principal,
            request=ContextBuildRequest(
                goal=message, capability_ids=("business.question_answering",), tool_ids=requested
            ),
            correlation_id=correlation_id,
        )
        status = "COMPLETED"
        error: PlatformError | None = None
        try:
            if context.status != "ACTIVE":
                answer = response(
                    context.status, "Kewenangan atau konteks ruang kerja belum tersedia."
                )
            elif set(requested) - set(context.allowed_tools):
                answer = response(
                    "DENIED", "Anda tidak memiliki kewenangan untuk sumber yang diminta."
                )
            elif (
                requested
                and any(word in message.casefold() for word in ("analisis", "research", "riset"))
                and (
                    "research.request" not in principal.permissions
                    or "research.management" not in principal.scopes
                )
            ):
                answer = response(
                    "DENIED", "Izin research.request dan scope research.management diperlukan."
                )
            elif any(
                (BUSINESS_TOOLS[tool][1] == "detail" or BUSINESS_TOOLS[tool][1].startswith("get_"))
                and (not reference or reference["domain"] != BUSINESS_TOOLS[tool][0].upper())
                for tool in requested
            ):
                answer = response(
                    "NEEDS_INFO", "Pilih referensi sumber dan pengenal bisnis yang ingin dibaca."
                )
            elif not self.test_enabled:
                raise GenesisClientError(
                    "MODEL_ROUTE_UNAVAILABLE", "Runtime belum terhubung.", correlation_id, True
                )
            else:
                async with asyncio.timeout(ARA_BUDGET["timeout_seconds"]):
                    answer, status = await self._execute(
                        principal, thread_id, message, data, run_id, correlation_id, context
                    )
        except TimeoutError:
            status = "TIMED_OUT"
            answer = response("FAILED", "Run mencapai batas waktu. Silakan coba kembali.")
            try:
                await self.authority.fail_transport(run_id, code="ARA_TIMEOUT", timed_out=True)
            except RunAuthorityError:
                pass
        except GenesisClientError:
            status = "FAILED"
            answer = response("FAILED", "Layanan ARA belum tersedia. Silakan coba kembali.")
            error = PlatformError(
                "GENESIS_UNAVAILABLE", answer["answer"], status_code=503, retryable=True
            )
        except Exception:
            status = "FAILED"
            answer = response("FAILED", "Jawaban tidak dapat diverifikasi. Silakan coba kembali.")
            error = PlatformError(
                "ARA_RESULT_INVALID", answer["answer"], status_code=503, retryable=True
            )
            try:
                await self.authority.fail_transport(run_id, code="ARA_RESULT_INVALID")
            except RunAuthorityError:
                pass
        answer = self.validate("ara-response-projection", answer)
        result = await self.repository.finish(principal, thread_id, run_id, answer, status)
        await self.record(
            principal,
            run_id,
            "ara.run_completed" if status == "COMPLETED" else "ara.run_failed",
            correlation_id,
            status,
        )
        if answer.get("action_proposal"):
            await self.record(
                principal, run_id, "ara.action_proposed", correlation_id, "NEEDS_REVIEW"
            )
        if error:
            raise error
        return self.validate("ara-run-projection", result)

    async def _execute(
        self,
        principal: Principal,
        thread_id: str,
        message: str,
        data: dict[str, Any],
        run_id: str,
        correlation_id: str,
        context: Any,
    ) -> tuple[dict[str, Any], str]:
        now = datetime.now(UTC)
        delegate = (
            bool(context.allowed_tools)
            and any(word in message.casefold() for word in ("analisis", "research", "riset"))
            and "research.request" in principal.permissions
            and "research.management" in principal.scopes
        )
        budget = {**ARA_BUDGET, "concurrency_limit": 1}
        if delegate:
            budget.update(max_depth=1, max_children=1)
        definition: dict[str, Any] = {
            "agent_id": "ara.workspace-assistant",
            "agent_version": "1.0.0",
            "name": "ARA",
            "purpose": "Read scoped canonical business sources and propose human review.",
            "capability_ids": list(ARA_CAPABILITIES),
            "skill_refs": [],
            "model_policy_ref": "ara.deterministic",
            "tool_ids": list(BUSINESS_TOOLS),
            "permission_refs": [],
            "scope_refs": list(context.scope_refs),
            "execution_budget": budget,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "delegation_policy": {
                "enabled": delegate,
                "max_depth": int(delegate),
                "max_children": int(delegate),
            },
        }
        digest = hashlib.sha256(
            json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        agent = RegistryEntry(
            subject_type="agent",
            subject_id=definition["agent_id"],
            version="1.0.0",
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            payload=definition,
            digest=digest,
            state=RegistryState.DRAFT,
            created_by="system",
            correlation_id=correlation_id,
            created_at=now,
        )
        execution = {
            **{
                key: getattr(principal, key)
                for key in ("tenant_id", "organization_id", "workspace_id", "actor_id")
            },
            "authority_context": {
                "role": sorted(principal.roles)[0] if principal.roles else "REQUESTER",
                "role_refs": sorted(principal.roles),
                "authority_level": "REQUESTER",
            },
            "scope_refs": list(context.scope_refs),
            "permission_refs": list(context.permission_refs),
            "data_scope": principal.data_scope.value,
            "data_classification": context.data_classification,
            "allowed_tool_ids": list(context.allowed_tools),
            "correlation_id": correlation_id,
            "execution_budget": budget,
        }
        for key in ("division_id", "project_id"):
            if getattr(principal, key):
                execution[key] = getattr(principal, key)
        history = await self.repository.messages(principal, thread_id)
        bundle = {
            **{
                key: execution[key]
                for key in (
                    "tenant_id",
                    "organization_id",
                    "workspace_id",
                    "actor_id",
                    "correlation_id",
                    "scope_refs",
                    "permission_refs",
                    "data_classification",
                    "allowed_tool_ids",
                )
            },
            "context_id": context.context_id,
            "created_at": now.isoformat(),
            "items": [],
            "evidence_refs": [],
            "execution_budget": budget,
            "allowed_capability_ids": list(context.allowed_capabilities),
            "data_scope": principal.data_scope.value,
        }
        for key in ("division_id", "project_id"):
            if getattr(principal, key):
                bundle[key] = getattr(principal, key)
        if self.memory is not None:
            # History is never promoted to organizational memory or canonical business truth.
            bundle["memory_refs"] = [
                record.memory_id for record in await self.conversation_memory(principal, thread_id)
            ]
        self.contracts.validate(
            "https://schemas.alos.dev/v1/context/context-bundle.schema.json", bundle
        )
        run_request = {
            "run_id": run_id,
            "root_run_id": run_id,
            "agent_id": definition["agent_id"],
            "agent_version": "1.0.0",
            "capability_id": "business.question_answering",
            "execution_context": execution,
            "context_bundle": bundle,
            "requested_tool_ids": list(context.allowed_tools),
            "execution_mode": "TEST",
            "input": {
                "message": message,
                "thread_id": thread_id,
                "history": [
                    {
                        "role": item["role"],
                        "content": item["content"][:1000],
                        "instruction_authority": False,
                    }
                    for item in history[-6:]
                ],
                "tool_arguments": {
                    tool: {"resource_id": data["business_reference"]["resource_id"]}
                    if BUSINESS_TOOLS[tool][1] == "detail"
                    or BUSINESS_TOOLS[tool][1].startswith("get_")
                    else {}
                    for tool in context.allowed_tools
                },
                **(
                    {"business_reference": data["business_reference"]}
                    if data.get("business_reference")
                    else {}
                ),
            },
        }
        started = await self.authority.begin(run_request, agent=agent)
        invocation = {
            "agent_definition": definition,
            "run_request": run_request,
            "runtime_authorization": {
                "run_id": run_id,
                "registry_digest": digest,
                "lifecycle_state": started.lifecycle_authorization,
                "allowed_tool_ids": list(started.authorized_tool_ids),
            },
        }
        try:
            result = await self.genesis.create_agent_run(
                invocation,
                correlation_id=correlation_id,
                timeout_seconds=ARA_BUDGET["timeout_seconds"],
            )
        except Exception:
            await self.authority.fail_transport(run_id, code="GENESIS_UNAVAILABLE")
            raise
        delegated = None
        if delegate and result["status"] == "COMPLETED":
            try:
                delegated = await read_child(
                    authority=self.authority,
                    genesis=self.genesis,
                    parent=run_request,
                    parent_result=result,
                    definition=definition,
                )
            except Exception:
                await self.authority.fail_transport(run_id, code="DELEGATION_FAILED")
                raise
            await self.record(
                principal,
                delegated["run_id"],
                "ara.child_completed",
                correlation_id,
                delegated["status"],
            )
            if delegated["status"] != "COMPLETED":
                result = {
                    **result,
                    "status": delegated["status"],
                    "error": delegated.get(
                        "error",
                        {
                            "code": "CHILD_FAILED",
                            "message": "Child source was not verified.",
                            "correlation_id": correlation_id,
                            "retryable": False,
                        },
                    ),
                }
        if result["status"] != "COMPLETED":
            completed = await self.authority.complete(result)
            result["status"] = completed.status.value
            kind = (
                "DENIED"
                if result.get("error", {}).get("code") in {"TOOL_DENIED", "TOOL_REJECTED"}
                else "FAILED"
            )
            failed_answer = response(
                kind,
                "Kewenangan ditolak."
                if kind == "DENIED"
                else "Sumber gagal dibaca atau run dihentikan. Silakan coba kembali.",
            )
            failed_answer["failed_sources"] = [
                tool["tool_id"]
                for tool in result.get("tool_results", [])
                if tool["status"] != "SUCCESS"
            ]
            return failed_answer, result["status"]
        answer = self.validate("ara-response-projection", result["output"])
        if delegated is not None:
            answer["delegation_result"] = delegated
        sources = []
        for tool in result.get("tool_results", []):
            if tool["status"] != "SUCCESS" or tool["tool_id"] not in context.allowed_tools:
                continue
            for evidence in tool.get("evidence_refs", []):
                if any(
                    evidence.get(key) != execution[key]
                    for key in ("tenant_id", "organization_id", "workspace_id", "correlation_id")
                ):
                    raise ValueError("Evidence authority mismatch")
                if (
                    evidence.get("run_id") != run_id
                    or evidence.get("instruction_authority") is not False
                ):
                    raise ValueError("Evidence lineage mismatch")
                sources.append(
                    {
                        "tool_call_id": tool["tool_call_id"],
                        "tool_id": tool["tool_id"],
                        "domain": BUSINESS_TOOLS[tool["tool_id"]][0],
                        "evidence_ref": evidence,
                        "source_ref": evidence["source_id"],
                        "freshness": "CURRENT",
                    }
                )
        answer["sources"] = sources
        if context.allowed_tools and not sources:
            raise ValueError("Business answer requires sources")
        if "buat agent" in message.casefold() and self.factory is not None:
            draft = await self.factory.analyze(
                {"requirement": message},
                principal=principal,
                correlation_id=correlation_id,
                proposal_only=True,
            )
            answer["factory_draft"] = draft
            answer["response_type"] = "NEEDS_REVIEW"
            answer["answer"] = (
                "Draft kapabilitas tersedia untuk tinjauan manusia; "
                "belum didaftarkan atau diaktifkan."
            )
            answer["action_proposal"] = {
                "proposal_id": f"proposal_{uuid4().hex}",
                "kind": "CAPABILITY_DRAFT",
                "status": "NEEDS_REVIEW",
                "summary": message,
                "required_permission": "capability.propose",
                "executed": False,
            }
        if (
            any(word in message.casefold() for word in ("analisis", "research", "riset"))
            and sources
        ):
            if (
                "research.request" not in principal.permissions
                or "research.management" not in principal.scopes
            ):
                return response(
                    "DENIED", "Izin research.request dan scope research.management diperlukan."
                ), "COMPLETED"
            evidence = sources[0]["evidence_ref"]
            research_bundle = {
                **bundle,
                "evidence_refs": [evidence],
                "items": [
                    {
                        "key": "canonical_business_snapshot",
                        "value": result["tool_results"][0]["output"]["data"],
                        **{
                            key: evidence[key]
                            for key in (
                                "source_id",
                                "evidence_id",
                                "source_version",
                                "content_hash",
                                "anchor",
                                "data_classification",
                            )
                        },
                        "source_type": "INTERNAL",
                        "freshness": "CURRENT",
                        "instruction_authority": False,
                    }
                ],
            }
            research = await self.genesis.execute_research(
                {
                    "research_id": f"research_{uuid4().hex}",
                    "run_id": run_id,
                    "correlation_id": correlation_id,
                    "domain": "MANAGEMENT",
                    "question": message,
                    "execution_context": execution,
                    "scope": list(context.scope_refs),
                    "context_bundle": research_bundle,
                },
                correlation_id=correlation_id,
            )
            research = self.contracts.validate(
                "https://schemas.alos.dev/v1/research/research-result.schema.json", research
            )
            if any(
                research[key] != execution[key]
                for key in ("tenant_id", "organization_id", "workspace_id", "correlation_id")
            ):
                raise ValueError("Research authority mismatch")
            if research["run_id"] != run_id:
                raise ValueError("Research lineage mismatch")
            answer.update(response_type="NEEDS_REVIEW", research_result=research)
            answer["answer"] += (
                "\n\nAnalisis internal tersedia sebagai rekomendasi advisory "
                "untuk tinjauan manusia."
            )
            answer["action_proposal"] = {
                "proposal_id": f"proposal_{uuid4().hex}",
                "kind": "RESEARCH",
                "status": "NEEDS_REVIEW",
                "summary": message,
                "required_permission": "research.request",
                "executed": False,
            }
        if answer.get("action_proposal"):
            answer["review_package"] = await review_proposal(
                proposal=answer["action_proposal"],
                principal=principal,
                run_id=run_id,
                correlation_id=correlation_id,
                genesis=self.genesis,
                contracts=self.contracts,
                evidence_registry=self.evidence_registry,
                proposal_context={
                    key: answer[key]
                    for key in ("factory_draft", "research_result")
                    if key in answer
                },
            )
            await self.record(
                principal, run_id, "ara.review_requested", correlation_id, "NEEDS_REVIEW"
            )
        await self.repository.persist_advisory(principal, answer, self.contracts.contract_version)
        answer = self.validate("ara-response-projection", answer)
        completed = await self.authority.complete({**result, "output": answer})
        if completed.status.value != "COMPLETED":
            return response(
                "FAILED", "Run dibatalkan atau mencapai batas eksekusi."
            ), completed.status.value
        return answer, "COMPLETED"
