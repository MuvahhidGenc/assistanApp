from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from hermes.config.settings import RiskLevel


@dataclass
class ToolDefinition:
    name: str
    description: str
    risk_level: RiskLevel
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    category: str = "general"


@dataclass
class ToolExecutionResult:
    success: bool
    output: Any = None
    error: str | None = None
    verified: bool = False


class BaseTool(ABC):
    """Base class for all local Windows tools."""

    name: str = ""
    description: str = ""
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    category: str = "general"

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
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}
