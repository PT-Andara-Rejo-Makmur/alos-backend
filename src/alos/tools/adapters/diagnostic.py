"""Non-production read-only diagnostic adapter used only by integration tests."""

from collections.abc import Mapping
from typing import Any


class DiagnosticEchoAdapter:
    """NON-PRODUCTION TEST TOOL. Returns input without I/O or side effects."""

    async def execute(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"echo": dict(arguments), "label": "NON-PRODUCTION TEST TOOL"}
