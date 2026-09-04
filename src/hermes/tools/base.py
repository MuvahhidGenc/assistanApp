from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.execution_target import ExecutionTarget

_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def schema_from_signature(func: Callable[..., Any]) -> dict[str, Any]:
    """Describe a callable's keyword parameters as a JSON schema."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, parameter in signature.parameters.items():
        if name == "self" or parameter.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue

        annotation = parameter.annotation
        entry: dict[str, Any] = {}
        json_type = _JSON_TYPES.get(annotation)
        if json_type is None and isinstance(annotation, str):
            json_type = _JSON_TYPES.get(
                {"str": str, "int": int, "float": float, "bool": bool}.get(annotation)
            )
        if json_type:
            entry["type"] = json_type

        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        elif parameter.default is not None:
            entry["default"] = parameter.default

        properties[name] = entry

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


@dataclass
class ToolDefinition:
    name: str
    description: str
    risk_level: RiskLevel
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    category: str = "general"
    execution_target: ExecutionTarget = ExecutionTarget.CLIENT
    capabilities: tuple[str, ...] = ()
    fallback_tools: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()


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

    # A tool may declare what it can do; otherwise the capability table in
    # `hermes.tools.capabilities` supplies it for the existing fleet.
    capabilities: tuple[str, ...] = ()
    fallback_tools: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        ...

    def get_definition(self) -> ToolDefinition:
        from hermes.tools.capabilities import (
            capabilities_for,
            fallbacks_for,
            prerequisites_for,
        )

        return ToolDefinition(
            name=self.name,
            description=self.description,
            risk_level=self.risk_level,
            parameters_schema=self.get_parameters_schema(),
            category=self.category,
            execution_target=self.execution_target,
            capabilities=capabilities_for(self.name, self.capabilities),
            fallback_tools=fallbacks_for(self.name, self.fallback_tools),
            prerequisites=prerequisites_for(self.name, self.prerequisites),
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        """What this tool accepts, read from `execute` unless declared.

        A tool that takes arguments but reports none is invisible to anything
        choosing tools by their inputs, and callers end up invoking it bare.
        Deriving the schema from the signature keeps the two from drifting
        apart; override this to add descriptions or constraints.
        """
        return schema_from_signature(type(self).execute)
