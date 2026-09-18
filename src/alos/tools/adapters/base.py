from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ToolInputError(ValueError):
    """Raised when arguments do not satisfy a tool-specific input contract."""


class ToolAdapter(Protocol):
    def validate_arguments(self, arguments: Mapping[str, Any]) -> None: ...

    async def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        execution_context: Mapping[str, Any],
    ) -> Any: ...
