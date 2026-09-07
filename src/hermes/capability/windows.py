"""Windows capability discovery.

A read-only helper that lists every capability the current Windows fleet
advertises, plus the tool names that implement each one. This is the
entry point for ReasoningRuntime to ask "what does this machine have?".

The module does not route, plan, or execute. It only inspects the
registry's contracts and groups them by execution target.
"""

from __future__ import annotations

from dataclasses import dataclass

from hermes.capability.contract import CapabilityContract
from hermes.capability.registry import CapabilityRegistry
from hermes.tools.execution_target import ExecutionTarget


@dataclass(frozen=True)
class WindowsCapabilitySummary:
    """A flat summary of one capability on the local machine.

    ReasoningRuntime reads these to decide whether a goal is locally
    satisfiable, partially satisfiable, or unsatisfiable here. It does
    *not* decide the goal itself.
    """

    capability: str
    default_tool: str
    allowed_overrides: tuple[str, ...]
    risk_level: str
    execution_target: str
    has_verifier: bool


def windows_capabilities(
    registry: CapabilityRegistry,
) -> list[WindowsCapabilitySummary]:
    """List every capability the registry advertises on this Windows client.

    Capabilities whose `execution_target` is `SERVER` are included too, so
    ReasoningRuntime can refuse them with a clear "this is server-only"
    rather than discovering the constraint only at execution time.
    """
    summaries: list[WindowsCapabilitySummary] = []
    for contract in registry.all():
        contract: CapabilityContract
        summaries.append(
            WindowsCapabilitySummary(
                capability=contract.name,
                default_tool=contract.default_tool,
                allowed_overrides=contract.allowed_overrides,
                risk_level=contract.risk_level.value,
                execution_target=contract.execution_target.value,
                has_verifier=registry.verifier_for(contract.name) is not None,
            )
        )
    return summaries


def windows_capability_names(
    registry: CapabilityRegistry,
    target: ExecutionTarget = ExecutionTarget.CLIENT,
) -> tuple[str, ...]:
    """Filter to capabilities that can execute on the requested target."""
    return tuple(
        summary.capability
        for summary in windows_capabilities(registry)
        if summary.execution_target == target.value
    )