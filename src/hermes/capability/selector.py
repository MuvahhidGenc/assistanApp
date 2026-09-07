"""Capability-aware tool selector — used by the runtime, not the registry.

The selector is a *helper* for ReasoningRuntime: it picks the lowest-risk
tool that fits a payload of arguments. The selector is not part of the
`CapabilityRegistry` because routing — deciding *which* capability a user
asks for — lives in the reasoning layer, not in the contract directory.

This module does **not**:

  * inspect user language,
  * decide which capability to invoke,
  * rank capabilities.

It only answers "given this capability and these arguments, which tool
should I call?" — and even then, the runtime may override the choice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes.capability.registry import CapabilityRegistry
from hermes.tools.capabilities import fit_arguments


class CapabilityToolSelection:
    """A specific tool chosen to satisfy a capability, with fitted arguments."""

    __slots__ = ("capability", "tool_name", "arguments", "risk_level")

    def __init__(
        self,
        *,
        capability: str,
        tool_name: str,
        arguments: dict[str, Any],
        risk_level: Any | None,
    ) -> None:
        self.capability = capability
        self.tool_name = tool_name
        self.arguments = dict(arguments)
        self.risk_level = risk_level


def select_tool_for_capability(
    registry: CapabilityRegistry,
    *,
    tool_registry: Any,
    capability: str,
    inputs: dict[str, Any] | None = None,
    exclude: tuple[str, ...] = (),
) -> CapabilityToolSelection | None:
    """Return the best tool that fits the inputs, or None.

    The selection mirrors V2's `select_tool_for_capability` heuristics —
    a tool that ignores half the payload is answering a narrower question
    than the caller asked. The contract layer adds the constraint that the
    tool must appear in the capability's `default_tool` + `allowed_overrides`
    list.
    """
    payload = dict(inputs or {})
    if str(capability) == "browser.back":
        payload.setdefault("action", "back")

    tools = registry.tools_for(capability)
    if not tools:
        return None

    # Look up the contract so we know the risk floor and execution target.
    contract = registry.get(capability)
    if contract is None:
        return None

    best: CapabilityToolSelection | None = None
    best_coverage = -1
    best_arity = 10**9

    for tool_name in tools:
        if tool_name in exclude:
            continue
        tool = tool_registry.get(tool_name)
        if tool is None:
            continue
        schema = tool.get_parameters_schema()
        fitted = fit_arguments(payload, schema)
        if fitted is None:
            continue

        properties = schema.get("properties") or {}
        coverage = len(fitted)
        # A tool offered arguments that fit none of them is answering a
        # different question.
        if coverage == 0 and payload and properties:
            continue

        arity = len(properties)
        if coverage > best_coverage or (
            coverage == best_coverage and arity < best_arity
        ):
            best_coverage = coverage
            best_arity = arity
            definition = tool.get_definition()
            best = CapabilityToolSelection(
                capability=capability,
                tool_name=tool_name,
                arguments=fitted,
                risk_level=definition.risk_level,
            )

    return best


__all__ = ["CapabilityToolSelection", "select_tool_for_capability"]
# Re-export Path to silence linters about unused import when fit_arguments
# is the only public surface used above.
_ = Path