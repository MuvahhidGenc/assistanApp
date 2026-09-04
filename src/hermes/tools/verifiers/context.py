from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hermes.server.models import ToolResultPayload

if TYPE_CHECKING:
    # Importing this at runtime pulls in hermes.mission.engine, which imports
    # this package back — the cycle silently disabled executor verification.
    from hermes.mission.models import MissionStep

ObserveToolCallback = Callable[[str, dict[str, Any], str], Awaitable[ToolResultPayload | None]]


@dataclass
class VerifierContext:
    tool_name: str
    tool_arguments: dict[str, Any]
    execution_success: bool
    execution_output: Any = None
    execution_error: str | None = None
    step: "MissionStep | None" = None
    run_id: str = ""
    observe_tool: ObserveToolCallback | None = None
    timeout_seconds: float = 10.0

    @property
    def expected_result(self) -> str | None:
        if self.step and self.step.expected_result:
            return self.step.expected_result
        return None

    @property
    def verification_required(self) -> bool:
        if self.step is None:
            return True
        required = self.step.verification.get("required")
        if required is False:
            return False
        return True

    @property
    def verification_method(self) -> str | None:
        if self.step and self.step.verification:
            method = self.step.verification.get("method")
            if isinstance(method, str) and method.strip():
                return method.strip()
        return None
