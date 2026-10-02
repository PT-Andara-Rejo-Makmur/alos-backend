"""Backend classification and capability policy; browser input grants nothing."""

from alos.identity import Principal

ARA_CAPABILITIES = frozenset(
    {
        "business.question_answering",
        "business.summary",
        "business.analysis",
        "business.navigation",
        "business.action_proposal",
    }
)
ARA_BUDGET = {
    "max_steps": 16,
    "max_tool_calls": 12,
    "max_tokens": 12000,
    "max_cost": 0,
    "timeout_seconds": 30,
    "max_depth": 0,
    "max_children": 0,
    "concurrency_limit": 1,
}


def maximum_classification(principal: Principal) -> str:
    # Exact canonical classification grant, never role/workspace/URL inference.
    return "RESTRICTED" if "restricted.access" in principal.permissions else "INTERNAL"
