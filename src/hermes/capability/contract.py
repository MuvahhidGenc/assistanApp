"""Capability contract data classes.

A `CapabilityContract` describes *one* thing the system can do — independent
of how many tools implement it, and independent of any user phrasing. It is
the single source of truth for ReasoningRuntime to ask "what does the
machine have for this?" without naming tools.

The contract fields are deliberately generic: any capability (filesystem,
network, screen, browser, document, application, etc.) fits the same shape.
This keeps the registry from accumulating per-capability branches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.execution_target import ExecutionTarget


class CapabilityFailureSemantics(StrEnum):
    """What kinds of failure the capability can produce, in terms ReasoningRuntime can act on.

    This is *not* the exception type — it is the class of outcome ReasoningRuntime
    should plan around. The registry does not decide what to do about it; that
    is the reasoning layer's job.
    """

    IDEMPOTENT = "idempotent"        # Repeating has the same effect.
    REPLAY_SAFE = "replay_safe"      # Repeating is safe up to a budget.
    SIDE_EFFECTING = "side_effecting"  # Repeating changes state.
    OBSERVABLE_ONLY = "observable_only"  # Cannot change state.


class CapabilitySideEffect(StrEnum):
    """What the capability does to the world, named so the registry is honest.

    The set is closed so a contract cannot quietly claim a new side effect
    category without the registry noticing.
    """

    NONE = "none"
    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    PROCESS_START = "process_start"
    PROCESS_KILL = "process_kill"
    NETWORK_REQUEST = "network_request"
    NETWORK_CONFIG = "network_config"
    SERVICE_CONTROL = "service_control"
    USER_INPUT = "user_input"           # Keyboard / mouse.
    WINDOW_MANIPULATION = "window_manipulation"
    CLIPBOARD = "clipboard"
    INSTALL = "install"
    REGISTRY_WRITE = "registry_write"
    OBSERVATION = "observation"         # Read-only state capture.


@dataclass(frozen=True)
class VerificationStrategy:
    """How the runtime should confirm a capability's effect was real.

    `method` is a short stable string the verifier code recognises
    (e.g. "filesystem.exists", "screen.text_present",
    "process.running", "generic.observe"). The runtime never assumes a
    verifier exists; it asks the registry.
    """

    method: str
    timeout_seconds: float = 10.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityContract:
    """The shape of one capability the system advertises.

    Fields are immutable. The contract does not reference any concrete tool
    or user message — it describes what the machine can do, not how a
    caller chooses to use it.
    """

    name: str
    purpose: str
    input_schema: dict[str, Any]                # JSON-schema-like dict.
    output_schema: dict[str, Any]               # JSON-schema-like dict.
    preconditions: tuple[str, ...] = ()
    known_limitations: tuple[str, ...] = ()
    side_effects: tuple[CapabilitySideEffect, ...] = ()
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    estimated_cost: str = "low"                 # "low" | "medium" | "high" — UI hint.
    observation_value: tuple[str, ...] = ()     # Conventional output fields the caller can bind.
    failure_semantics: CapabilityFailureSemantics = CapabilityFailureSemantics.OBSERVABLE_ONLY
    execution_target: ExecutionTarget = ExecutionTarget.CLIENT
    default_tool: str = ""                      # Tool name; may be "" if multiple tools equally fit.
    allowed_overrides: tuple[str, ...] = ()     # Other tool names that satisfy this capability.
    verification: VerificationStrategy | None = None

    # Internal — not part of the public contract surface but populated by the
    # default builder so external callers can introspect where the values came
    # from. Kept off the docstring to avoid suggesting callers depend on it.
    _source: str = "explicit"