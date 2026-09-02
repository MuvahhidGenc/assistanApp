from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.execution_target import ExecutionTarget


@dataclass
class ToolDefinition:
    name: str
    description: str
    risk_level: RiskLevel
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    category: str = "general"
    execution_target: ExecutionTarget = ExecutionTarget.CLIENT


@dataclass
class ToolExecutionResult:
    success: bool
    output: Any = None
    error: str | None = None
    verified: bool = False
    execution_status: str | None = None
    observation: dict[str, Any] | None = None
    verification_status: str | None = None
    verification_method: str | None = None
    verification_details: dict[str, Any] | None = None
    verified_at: str | None = None


class BaseTool(ABC):
    """Base class for all local Windows tools."""

    name: str = ""
    description: str = ""
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    category: str = "general"
    execution_target: ExecutionTarget = ExecutionTarget.CLIENT

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        ...

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            risk_level=self.risk_level,
            parameters_schema=self.get_parameters_schema(),
            category=self.category,
            execution_target=self.execution_target,
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}
