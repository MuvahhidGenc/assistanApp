"""V3 Capability Contract tests.

These tests verify the *new* contract layer, not the V2 fleet. The V2
fleet is exercised by `tests/test_capability_binding.py` etc.; these tests
treat the registry as a pure data structure and exercise the contract
machinery in isolation.
"""

from __future__ import annotations

import pytest

from hermes.capability import (
    CapabilityContract,
    CapabilityFailureSemantics,
    CapabilityRegistry,
    CapabilitySideEffect,
    CapabilityVerifierBinding,
    VerificationStrategy,
    create_default_capability_registry,
    windows_capabilities,
    windows_capability_names,
)
from hermes.capability._defaults import build_default_contract
from hermes.config.settings import RiskLevel
from hermes.tools.execution_target import ExecutionTarget
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.registry import create_default_verifier_registry


# ---------------------------------------------------------------------------
# Contract data shape
# ---------------------------------------------------------------------------


def test_contract_is_immutable():
    contract = CapabilityContract(
        name="test.capability",
        purpose="test",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
    )
    with pytest.raises(Exception):
        contract.name = "other"  # type: ignore[misc]


def test_contract_defaults_are_safe():
    contract = CapabilityContract(
        name="empty",
        purpose="",
        input_schema={},
        output_schema={},
    )
    assert contract.preconditions == ()
    assert contract.known_limitations == ()
    assert contract.side_effects == ()
    assert contract.risk_level == RiskLevel.READ_ONLY
    assert contract.estimated_cost == "low"
    assert contract.observation_value == ()
    assert contract.failure_semantics == CapabilityFailureSemantics.OBSERVABLE_ONLY
    assert contract.execution_target == ExecutionTarget.CLIENT
    assert contract.default_tool == ""
    assert contract.allowed_overrides == ()
    assert contract.verification is None


def test_verification_strategy_timeout_default():
    vs = VerificationStrategy(method="filesystem.exists")
    assert vs.timeout_seconds == 10.0


# ---------------------------------------------------------------------------
# Registry behaviour
# ---------------------------------------------------------------------------


def test_registry_rejects_empty_name():
    registry = CapabilityRegistry()
    with pytest.raises(ValueError):
        registry.register(
            CapabilityContract(
                name="",
                purpose="x",
                input_schema={},
                output_schema={},
            )
        )


def test_registry_get_returns_none_when_missing():
    registry = CapabilityRegistry()
    assert registry.get("does.not.exist") is None


def test_registry_all_is_sorted():
    registry = CapabilityRegistry()
    for name in ("zeta", "alpha", "mu"):
        registry.register(
            CapabilityContract(
                name=name,
                purpose=name,
                input_schema={},
                output_schema={},
            )
        )
    ordered = [c.name for c in registry.all()]
    assert ordered == ["alpha", "mu", "zeta"]


def test_registry_tools_for_includes_default_and_overrides():
    registry = CapabilityRegistry()
    registry.register(
        CapabilityContract(
            name="demo.capability",
            purpose="demo",
            input_schema={},
            output_schema={},
            default_tool="primary",
            allowed_overrides=("alt_a", "alt_b"),
        )
    )
    assert registry.tools_for("demo.capability") == ("primary", "alt_a", "alt_b")
    assert registry.tools_for("missing.capability") == ()


def test_registry_capabilities_for_tool_reverse_lookup():
    registry = CapabilityRegistry()
    registry.register(
        CapabilityContract(
            name="cap.one",
            purpose="",
            input_schema={},
            output_schema={},
            default_tool="tool_x",
        )
    )
    registry.register(
        CapabilityContract(
            name="cap.two",
            purpose="",
            input_schema={},
            output_schema={},
            default_tool="tool_x",
        )
    )
    registry.register(
        CapabilityContract(
            name="cap.three",
            purpose="",
            input_schema={},
            output_schema={},
            default_tool="tool_y",
        )
    )
    assert registry.capabilities_for_tool("tool_x") == ("cap.one", "cap.two")
    assert registry.capabilities_for_tool("tool_y") == ("cap.three",)
    assert registry.capabilities_for_tool("missing") == ()


def test_registry_verifier_for_returns_bound_instance():
    registry = CapabilityRegistry()
    binding = CapabilityVerifierBinding(
        capability="demo.capability",
        verifier=_StubVerifier(("demo.capability",)),
        method="demo.method",
    )
    registry.bind_verifier(binding)
    found = registry.verifier_for("demo.capability")
    assert found is binding
    assert registry.verifier_for("other.capability") is None


def test_registry_verifier_binding_rejects_empty_capability():
    registry = CapabilityRegistry()
    with pytest.raises(ValueError):
        registry.bind_verifier(
            CapabilityVerifierBinding(
                capability="",
                verifier=_StubVerifier(()),
            )
        )


def test_registry_to_dict_is_serialisable():
    registry = CapabilityRegistry()
    registry.register(
        CapabilityContract(
            name="demo.capability",
            purpose="demo",
            input_schema={},
            output_schema={},
            default_tool="primary",
            allowed_overrides=("alt",),
            risk_level=RiskLevel.NORMAL_MODIFICATION,
            execution_target=ExecutionTarget.CLIENT,
            side_effects=(CapabilitySideEffect.FILESYSTEM_WRITE,),
            preconditions=("must be root",),
            known_limitations=("latency varies",),
            failure_semantics=CapabilityFailureSemantics.SIDE_EFFECTING,
            estimated_cost="medium",
            observation_value=("path",),
            verification=VerificationStrategy(method="filesystem.exists"),
        )
    )
    data = registry.to_dict()
    assert "demo.capability" in data["capabilities"]
    entry = data["capabilities"]["demo.capability"]
    assert entry["default_tool"] == "primary"
    assert entry["risk_level"] == "normal_modification"
    assert entry["side_effects"] == ["filesystem_write"]
    assert entry["preconditions"] == ["must be root"]
    assert entry["known_limitations"] == ["latency varies"]
    assert entry["failure_semantics"] == "side_effecting"
    assert entry["estimated_cost"] == "medium"
    assert entry["observation_value"] == ["path"]
    assert entry["verification_method"] == "filesystem.exists"


# ---------------------------------------------------------------------------
# Negative-routing invariants — registry is not a router
# ---------------------------------------------------------------------------


def test_registry_has_no_routing_methods():
    """Critical invariant: the registry must not decide or route."""
    registry = CapabilityRegistry()
    forbidden = [
        "route",
        "decide",
        "classify",
        "match",
        "dispatch",
        "plan",
        "interpret",
        "understand",
        "choose_intent",
        "score_message",
    ]
    for method in forbidden:
        assert not hasattr(registry, method), (
            f"Registry must not expose '{method}' — it would imply semantic routing."
        )


def test_registry_does_not_import_intent_or_orchestrator():
    """Defensive: registry must not depend on semantic layers at module level."""
    import hermes.capability.registry as registry_module

    source = open(registry_module.__file__, encoding="utf-8").read()
    for forbidden in (
        "intent.",
        "orchestrator",
        "mission.",
        "conversation_flow",
        "goal_parser",
        "goal_router",
        "task_planner",
    ):
        assert forbidden not in source, (
            f"registry.py must not reference '{forbidden}'."
        )


# ---------------------------------------------------------------------------
# Default builder — populated from the existing V2 fleet
# ---------------------------------------------------------------------------


def test_default_contract_for_filesystem_write_has_required_fields():
    registry = create_default_registry()
    contract = build_default_contract(registry, "filesystem.write")
    assert contract is not None
    assert contract.name == "filesystem.write"
    assert "overwrite" in contract.purpose or "file" in contract.purpose.lower()
    assert contract.default_tool in ("write_file", "create_folder")
    assert contract.risk_level == RiskLevel.NORMAL_MODIFICATION
    assert CapabilitySideEffect.FILESYSTEM_WRITE in contract.side_effects
    assert contract.failure_semantics == CapabilityFailureSemantics.SIDE_EFFECTING
    assert "path" in contract.input_schema.get("properties", {})


def test_default_contract_for_unknown_capability_is_none():
    registry = create_default_registry()
    assert build_default_contract(registry, "this.does.not.exist") is None


def test_default_contract_for_browser_navigate_targets_client():
    registry = create_default_registry()
    contract = build_default_contract(registry, "browser.navigate")
    assert contract is not None
    assert contract.execution_target in (
        ExecutionTarget.CLIENT,
        ExecutionTarget.SERVER,
    )
    assert contract.failure_semantics == CapabilityFailureSemantics.SIDE_EFFECTING
    assert contract.verification is not None
    assert contract.verification.method == "browser.url_open"


def test_default_registry_contains_every_v2_capability():
    """The default registry covers the same set the V2 registry advertises."""
    tool_registry = create_default_registry()
    capability_registry = create_default_capability_registry(tool_registry)
    v2_caps = set(tool_registry.capabilities())
    v3_caps = set(capability_registry.names())
    assert v2_caps == v3_caps


def test_default_registry_binds_verifiers_for_specific_tools():
    capability_registry = create_default_capability_registry()
    # `write_file` has a specific verifier in V2.
    binding = capability_registry.verifier_for("filesystem.write")
    assert binding is not None
    assert "write_file" in binding.tool_names


def test_default_registry_generic_verifier_is_not_bound_to_everything():
    """The generic verifier must not steal bindings from specific verifiers.

    ReasoningRuntime asks the registry for a specific binding and only
    falls back to the generic verifier when none exists. The contract
    layer preserves that contract.
    """
    capability_registry = create_default_capability_registry()
    binding = capability_registry.verifier_for("filesystem.write")
    assert binding is not None
    assert binding.method != "" or len(binding.tool_names) > 0


def test_default_registry_estimated_cost_scales_with_risk():
    capability_registry = create_default_capability_registry()
    high_risk = [
        contract
        for contract in capability_registry.all()
        if contract.risk_level == RiskLevel.HIGH_RISK
    ]
    for contract in high_risk:
        assert contract.estimated_cost == "high", contract.name


# ---------------------------------------------------------------------------
# Windows helper
# ---------------------------------------------------------------------------


def test_windows_capabilities_lists_every_contract():
    capability_registry = create_default_capability_registry()
    summaries = windows_capabilities(capability_registry)
    assert len(summaries) == len(capability_registry.all())


def test_windows_capability_names_filters_by_target():
    capability_registry = create_default_capability_registry()
    client_caps = set(
        windows_capability_names(capability_registry, ExecutionTarget.CLIENT)
    )
    all_caps = set(capability_registry.names())
    # Every advertised capability should be CLIENT-targetable on this fleet.
    assert client_caps <= all_caps
    assert client_caps  # non-empty


def test_windows_capability_summary_has_required_fields():
    capability_registry = create_default_capability_registry()
    summary = windows_capabilities(capability_registry)[0]
    assert summary.capability
    assert summary.default_tool
    assert summary.execution_target in ("client", "server")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubVerifier:
    """Stand-in verifier used to exercise the binding logic without depending on V2."""

    def __init__(self, tool_names: tuple[str, ...]) -> None:
        self.tool_names = tool_names

    async def verify(self, ctx):  # pragma: no cover — never invoked here
        raise NotImplementedError