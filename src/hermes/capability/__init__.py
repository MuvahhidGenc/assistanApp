"""V3 Capability Contract — a registry of what the system can do.

This package is a thin contract layer on top of the existing V2 tool fleet.
The registry holds *descriptions* of capabilities (purpose, preconditions,
risk, observation value, verification strategy) and binds each capability to
one or more concrete tool implementations and one or more verifiers.

It deliberately does **not**:
  * decide what the user meant,
  * route a message to a capability,
  * produce a workflow,
  * hold semantic state about a goal.

Those concerns live in ReasoningRuntime (Phase 3) and World Model (Phase 2).
The contract only describes *what exists* and *how it is observed and
verified*. The registry never executes a tool and never inspects user
language.
"""

from hermes.capability.contract import (
    CapabilityContract,
    CapabilityFailureSemantics,
    CapabilitySideEffect,
    VerificationStrategy,
)
from hermes.capability.registry import (
    CapabilityRegistry,
    create_default_capability_registry,
)
from hermes.capability.selector import (
    CapabilityToolSelection,
    select_tool_for_capability,
)
from hermes.capability.verifier import CapabilityVerifierBinding
from hermes.capability.windows import (
    WindowsCapabilitySummary,
    windows_capabilities,
    windows_capability_names,
)

__all__ = [
    "CapabilityContract",
    "CapabilityFailureSemantics",
    "CapabilityRegistry",
    "CapabilitySideEffect",
    "CapabilityToolSelection",
    "CapabilityVerifierBinding",
    "VerificationStrategy",
    "WindowsCapabilitySummary",
    "create_default_capability_registry",
    "select_tool_for_capability",
    "windows_capabilities",
    "windows_capability_names",
]