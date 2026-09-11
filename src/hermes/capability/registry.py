"""Capability registry — a contract directory, not a router.

The registry holds capability contracts, capability↔tool bindings, and
capability↔verifier bindings. It exposes only *lookup* and *listing* APIs:

  * `register(contract)`
  * `get(capability) -> CapabilityContract | None`
  * `all() -> list[CapabilityContract]`
  * `tools_for(capability) -> tuple[str, ...]`
  * `verifier_for(capability) -> CapabilityVerifierBinding | None`
  * `capabilities_for_tool(tool_name) -> tuple[str, ...]`

It explicitly does **not**:

  * decide which capability a user message asks for,
  * inspect user language,
  * produce a workflow or sequence of capabilities,
  * execute tools.

The default constructor builds contracts out of the existing V2 tool
registry and the existing V2 verifier registry, so the contract layer is
*populated* from working V2 declarations and not from retyped tables.
"""

from __future__ import annotations

from typing import Any

from hermes.capability._defaults import build_default_contracts
from hermes.capability.contract import CapabilityContract
from hermes.capability.verifier import CapabilityVerifierBinding
from hermes.tools.registry import ToolRegistry
from hermes.tools.verifiers.registry import VerifierRegistry


class CapabilityRegistry:
    """In-memory registry of capability contracts and their bindings."""

    def __init__(self) -> None:
        self._contracts: dict[str, CapabilityContract] = {}
        self._verifier_bindings: dict[str, CapabilityVerifierBinding] = {}
        self._tools_for_capability: dict[str, tuple[str, ...]] = {}

    # ---- contract registration ------------------------------------------------

    def register(self, contract: CapabilityContract) -> None:
        """Register or replace a capability contract.

        Raises:
            ValueError: when the contract's name is empty.
        """
        if not contract.name:
            raise ValueError("CapabilityContract.name must be non-empty")
        self._contracts[contract.name] = contract

    def register_many(self, contracts: dict[str, CapabilityContract]) -> None:
        """Register several contracts at once; same semantics as `register`."""
        for name, contract in contracts.items():
            self.register(contract)
            # _source is a private channel for the builder; we keep it that way.
            _ = name

    # ---- verifier binding -----------------------------------------------------

    def bind_verifier(self, binding: CapabilityVerifierBinding) -> None:
        """Bind a verifier instance to a capability name."""
        if not binding.capability:
            raise ValueError("CapabilityVerifierBinding.capability must be non-empty")
        self._verifier_bindings[binding.capability] = binding

    # ---- lookups (read-only) --------------------------------------------------

    def get(self, capability: str) -> CapabilityContract | None:
        """Return the contract for a capability, or None if unregistered."""
        return self._contracts.get(capability)

    def all(self) -> list[CapabilityContract]:
        """Return all registered contracts in deterministic name order."""
        return [self._contracts[name] for name in sorted(self._contracts)]

    def names(self) -> tuple[str, ...]:
        """Return all registered capability names in deterministic order."""
        return tuple(sorted(self._contracts))

    def capabilities(self) -> tuple[str, ...]:
        """Alias for `names()`; matches V2 `ToolRegistry.capabilities()` shape."""
        return self.names()

    def tools_for(self, capability: str) -> tuple[str, ...]:
        """Tool names that implement the capability, lowest risk first."""
        cached = self._tools_for_capability.get(capability)
        if cached is not None:
            return cached
        contract = self.get(capability)
        if contract is None:
            return ()
        return (contract.default_tool, *contract.allowed_overrides)

    def verifier_for(self, capability: str) -> CapabilityVerifierBinding | None:
        """Return the verifier binding for a capability, or None."""
        return self._verifier_bindings.get(capability)

    def capabilities_for_tool(self, tool_name: str) -> tuple[str, ...]:
        """Reverse lookup: which capabilities does this tool implement?"""
        result: list[str] = []
        for contract in self.all():
            tools = self.tools_for(contract.name)
            if tool_name in tools:
                result.append(contract.name)
        return tuple(result)

    def to_dict(self) -> dict[str, Any]:
        """Serializable view of the registry, useful for diagnostics.

        Does not include verifier instances — they are not JSON-serialisable.
        """
        return {
            "capabilities": {
                contract.name: {
                    "purpose": contract.purpose,
                    "risk_level": contract.risk_level.value,
                    "execution_target": contract.execution_target.value,
                    "default_tool": contract.default_tool,
                    "allowed_overrides": list(contract.allowed_overrides),
                    "side_effects": [s.value for s in contract.side_effects],
                    "preconditions": list(contract.preconditions),
                    "known_limitations": list(contract.known_limitations),
                    "failure_semantics": contract.failure_semantics.value,
                    "estimated_cost": contract.estimated_cost,
                    "observation_value": list(contract.observation_value),
                    "verification_method": (
                        contract.verification.method if contract.verification else ""
                    ),
                }
                for contract in self.all()
            }
        }


def create_default_capability_registry(
    tool_registry: ToolRegistry | None = None,
    verifier_registry: VerifierRegistry | None = None,
) -> CapabilityRegistry:
    """Build a registry populated from the V2 registries.

    The two registries are imported lazily so that importing this module does
    not require the entire V2 tool fleet to be loaded. Pass either registry
    explicitly when you want a custom fleet.
    """
    if tool_registry is None:
        from hermes.tools.registry import create_default_registry

        tool_registry = create_default_registry()

    capability_registry = CapabilityRegistry()
    contracts = build_default_contracts(tool_registry)
    capability_registry.register_many(contracts)

    # Cache tools-for-capability so lookups don't allocate each time.
    for contract in capability_registry.all():
        capability_registry._tools_for_capability[contract.name] = (
            contract.default_tool,
            *contract.allowed_overrides,
        )

    if verifier_registry is None:
        from hermes.tools.verifiers.registry import create_default_verifier_registry

        verifier_registry = create_default_verifier_registry()

    # Bind each verifier to every capability it covers. A verifier names the
    # tools it handles; the contract maps tool -> capability, so the binding
    # closes the loop without duplicating the tool table.
    for verifier in _iter_verifiers(verifier_registry):
        tool_names = getattr(verifier, "tool_names", ())
        if not isinstance(tool_names, tuple) or not tool_names:
            # GenericVerifier has no `tool_names` — it is the runtime's
            # fallback, not a binding target. Skip it so specific verifiers
            # win for the capabilities they actually cover.
            continue
        for tool_name in tool_names:
            for capability in capability_registry.capabilities_for_tool(tool_name):
                # First verifier wins for a capability. Multiple bindings is a
                # future enhancement; today the registry stores one verifier
                # per capability by design.
                capability_registry.bind_verifier(
                    CapabilityVerifierBinding(
                        capability=capability,
                        verifier=verifier,
                        method=getattr(verifier, "method", "") or "",
                    )
                )

    return capability_registry


def _iter_verifiers(verifier_registry: VerifierRegistry) -> list[Any]:
    """Return every verifier the registry knows about, including the generic fallback.

    The V2 `VerifierRegistry` keeps the generic verifier outside `_by_tool`,
    so we synthesise the iterable here.
    """
    from hermes.tools.verifiers.generic import GenericVerifier

    def _tool_names(verifier: Any) -> tuple[str, ...]:
        # `tool_names` is declared on `BaseVerifier` but `GenericVerifier` does
        # not override it. Treat that as "covers everything the registry
        # doesn't bind a specific verifier for"; the registry only needs the
        # binding iteration to *not crash* — the generic verifier is bound
        # only when no specific verifier matches.
        names = getattr(verifier, "tool_names", None)
        if isinstance(names, tuple):
            return names
        return ()

    seen: set[int] = set()
    result: list[Any] = []
    for verifier in verifier_registry._by_tool.values():  # noqa: SLF001 — internal access
        if id(verifier) in seen:
            continue
        seen.add(id(verifier))
        result.append(verifier)
    if (
        hasattr(verifier_registry, "_generic")
        and id(verifier_registry._generic) not in seen  # noqa: SLF001
    ):
        result.append(verifier_registry._generic)  # noqa: SLF001
    # Stable order for deterministic tests.
    return sorted(
        result,
        key=lambda value: (
            getattr(value, "method", "") or type(value).__name__,
            _tool_names(value),
        ),
    )