"""Research/domain skill definition built using the shared backend skill registry."""

from __future__ import annotations

RESEARCH_SKILL_IDS = (
    "skill.research.core",
    "skill.research.technology",
    "skill.research.property_business",
    "skill.research.management",
    "skill.research.property_market",
)


def build_research_skill_definition(
    *, skill_id: str = "skill.research.core", version: str = "1.0.0"
) -> dict[str, object]:
    if skill_id not in RESEARCH_SKILL_IDS:
        raise ValueError("research skill id is not canonical")
    return {
        "skill_id": skill_id,
        "skill_version": version,
        "name": "Research Domain Skill",
        "description": (
            "Plan research, select approved evidence sources, and synthesize "
            "findings using approved tools only."
        ),
        "purpose": (
            "Convert a domain question into methodical, evidence-backed research "
            "steps under backend authorization."
        ),
        "when_to_use": [
            "When a user needs structured research synthesis.",
            "When source selection must respect scope and authorization.",
        ],
        "input_schema_ref": "https://schemas.alos.dev/v1/research/research-request.schema.json",
        "output_schema_ref": "https://schemas.alos.dev/v1/research/domain-access-response.schema.json",
        "procedure": [
            "Resolve the request under the authenticated tenant and workspace scope.",
            "Select only approved tools and sources.",
            "Cite evidence and synthesize findings without bypassing ToolExecutor.",
        ],
        "required_tool_ids": ["tool.executor", "tool.research.execute"],
        "owner_actor_id": "actor_backend_authority",
        "risk_level": "MEDIUM",
        "permission_refs": ["sources.read", "research.read"],
        "scope_refs": ["scope.workspace.ops", "scope.sources.read"],
        "evidence_requirements": ["Evidence must be cited and attributable to approved sources."],
        "restrictions": [
            "No direct database access.",
            "No direct network access outside approved ToolExecutor routes.",
            "No permission expansion from prompt or AI output.",
        ],
        "failure_modes": [
            "Scope mismatch",
            "Insufficient evidence",
            "Unauthorized tool invocation",
        ],
        "escalation": ["Escalate to a human reviewer when scope or evidence is ambiguous."],
        "evaluation": ["Validate evidence trail and tool usage against authorization rules."],
    }
