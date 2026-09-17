from __future__ import annotations

from dataclasses import dataclass

from alos.tools.adapters.base import ToolAdapter


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    tool_id: str
    required_permission: str
    required_scopes: frozenset[str]
    adapter: ToolAdapter
    production_enabled: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolRegistration] = {}

    def register(self, registration: ToolRegistration) -> None:
        if registration.tool_id in self._tools:
            raise ValueError(f"Tool already registered: {registration.tool_id}")
        self._tools[registration.tool_id] = registration

    def get(self, tool_id: str) -> ToolRegistration | None:
        return self._tools.get(tool_id)
