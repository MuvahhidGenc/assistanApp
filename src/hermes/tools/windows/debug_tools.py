from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult


class EchoTool(BaseTool):
    """Debug tool for testing the tool pipeline."""

    name = "echo"
    description = "Echo back input (for testing)."
    risk_level = RiskLevel.READ_ONLY
    category = "debug"

    async def execute(self, message: str = "", **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(success=True, output={"echo": message}, verified=True)
