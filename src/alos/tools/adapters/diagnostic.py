"""Non-production read-only diagnostic adapter used only by integration tests."""

from collections.abc import Mapping
from typing import Any

from alos.tools.adapters.base import ToolInputError


class DiagnosticEchoAdapter:
    """NON-PRODUCTION TEST TOOL. Returns input without I/O or side effects."""

    def validate_arguments(self, arguments: Mapping[str, Any]) -> None:
        if set(arguments) != {"message"}:
            raise ToolInputError("diagnostic.echo accepts only the message field")
        message = arguments.get("message")
        if not isinstance(message, str) or not 1 <= len(message) <= 256:
            raise ToolInputError("message must be a string between 1 and 256 characters")

    async def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        execution_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        del execution_context
        return {"echo": dict(arguments), "label": "NON-PRODUCTION TEST TOOL"}
