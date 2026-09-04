"""Phase B/C — capability discovery, unified risk, execution guard.

These cover the two properties the agent transformation depends on: tools are
reachable by what they do rather than by name, and no mission can repeat itself
without bound.
"""
from __future__ import annotations

import pytest

from hermes.config.settings import RiskLevel
from hermes.mission.execution_guard import (
    DEFAULT_LIMITS,
    ExecutionGuard,
    GuardLimits,
    GuardStop,
    action_fingerprint,
    plan_fingerprint,
)
from hermes.mission.models import Mission, MissionStep
from hermes.mission.recovery.classifier import ErrorCategory
from hermes.mission.recovery.context import FailureKind, RecoveryContext
from hermes.mission.recovery.strategies import CapabilityFallbackStrategy
from hermes.security.policy_engine import PolicyDecision, PolicyEngine
from hermes.tools.capabilities import Capability
from hermes.tools.catalog import build_tool_catalog
from hermes.tools.registry import create_default_registry


@pytest.fixture(scope="module")
def registry():
    return create_default_registry()


# --- Phase B: capability discovery -------------------------------------


def test_every_registered_tool_declares_capabilities(registry):
    missing = [d.name for d in registry.list_tools() if not d.capabilities]
    assert missing == []


def test_tools_are_discoverable_by_what_they_do(registry):
    def names(capability):
        return [d.name for d in registry.find_by_capability(capability)]

    assert names(Capability.FILESYSTEM_SEARCH) == ["search_files"]
    assert "read_screen_text" in names(Capability.BROWSER_READ)
    assert set(names(Capability.APPLICATION_OPEN)) == {"open_app", "open_url", "open_path"}
    assert "install_program" in names(Capability.APPLICATION_INSTALL)


def test_capability_lookup_prefers_the_least_risky_tool(registry):
    matches = registry.find_by_capability(Capability.APPLICATION_OPEN)
    risks = [d.risk_level for d in matches]
    assert risks == sorted(risks, key=lambda r: [
        RiskLevel.READ_ONLY,
        RiskLevel.LOW_RISK,
        RiskLevel.NORMAL_MODIFICATION,
        RiskLevel.HIGH_RISK,
    ].index(r))


def test_unknown_capability_returns_nothing_rather_than_guessing(registry):
    assert registry.find_by_capability("teleport.user") == []


def test_capabilities_reach_the_server_manifest(registry):
    manifest = {entry["name"]: entry for entry in registry.to_manifest()}
    assert Capability.FILESYSTEM_SEARCH in manifest["search_files"]["capabilities"]
    assert manifest["read_file"]["prerequisites"]
    assert "screenshot" in manifest["read_screen_text"]["fallback_tools"]


def test_catalog_prerequisites_are_no_longer_empty(registry):
    catalog = build_tool_catalog(registry)
    entry = catalog.get("write_file")
    assert entry is not None
    assert entry.prerequisites
    assert Capability.FILESYSTEM_WRITE in entry.capabilities
    assert catalog.find_by_capability(Capability.FILESYSTEM_DELETE)


# --- Phase B: one risk taxonomy ----------------------------------------


def test_declared_and_enforced_risk_agree_for_every_tool(registry):
    policy = PolicyEngine(registry=registry)
    divergent = [
        d.name for d in registry.list_tools() if policy.classify_tool(d.name) != d.risk_level
    ]
    assert divergent == []


def test_rename_path_no_longer_skips_approval(registry):
    policy = PolicyEngine(registry=registry)
    # It modifies the filesystem but appeared in none of the legacy name lists,
    # so it used to fall through to READ_ONLY and execute unapproved.
    assert policy.classify_tool("rename_path") is RiskLevel.NORMAL_MODIFICATION
    assert policy.evaluate("rename_path", {"path": "a", "new_name": "b"}).decision is (
        PolicyDecision.REQUIRE_APPROVAL
    )


def test_navigation_tools_stay_approval_free(registry):
    policy = PolicyEngine(registry=registry)
    for tool in ("scroll", "click_text", "browser_nav"):
        assert policy.evaluate(tool, {}).decision is PolicyDecision.ALLOW


def test_unregistered_tool_names_still_fall_back_to_name_lists():
    policy = PolicyEngine(registry=create_default_registry())
    assert policy.classify_tool("uninstall_program") is RiskLevel.HIGH_RISK


# --- Phase B: capability-driven recovery -------------------------------


def _recovery_ctx(registry, tool_name, arguments, category=ErrorCategory.TOOL_FAILURE):
    mission = Mission(mission_id="m1", user_goal="test")
    step = MissionStep(
        step_id="s1", title="t", tool_name=tool_name, tool_arguments=dict(arguments)
    )
    return RecoveryContext(
        mission=mission,
        step=step,
        failure_kind=FailureKind.EXECUTION,
        error_category=category,
        error_text="boom",
        registry=registry,
    )


def test_recovery_switches_mechanism_instead_of_retrying(registry):
    ctx = _recovery_ctx(registry, "read_screen_text", {})
    actions = CapabilityFallbackStrategy().build_actions(ctx)

    assert [a.tool_name for a in actions] == ["screenshot"]
    assert actions[0].tool_name != ctx.step.tool_name


def test_recovery_skips_alternatives_it_cannot_actually_call(registry):
    ctx = _recovery_ctx(registry, "open_url", {"url": "https://example.com"})
    actions = CapabilityFallbackStrategy().build_actions(ctx)

    # open_url declares browser_nav, open_app and run_command as alternatives,
    # but open_app requires `app` and run_command requires a command, and a URL
    # cannot supply either. Only the callable one is proposed.
    assert [a.tool_name for a in actions] == ["browser_nav"]


def test_recovery_drops_arguments_the_alternative_cannot_accept(registry):
    ctx = _recovery_ctx(registry, "open_path", {"path": "C:/tmp"})
    by_tool = {
        a.tool_name: a.tool_arguments for a in CapabilityFallbackStrategy().build_actions(ctx)
    }

    # open_app accepts `app`, not `path`, so the incompatible argument is not
    # carried over — and since `app` is required, the fallback is dropped.
    assert "open_app" not in by_tool


def test_recovery_proposes_nothing_when_no_alternative_exists(registry):
    ctx = _recovery_ctx(registry, "write_file", {"path": "a.txt", "content": "x"})
    assert CapabilityFallbackStrategy().build_actions(ctx) == []


def test_recovery_needs_a_registry_to_reason_about_alternatives():
    mission = Mission(mission_id="m1", user_goal="test")
    step = MissionStep(step_id="s1", title="t", tool_name="open_url", tool_arguments={})
    ctx = RecoveryContext(
        mission=mission,
        step=step,
        failure_kind=FailureKind.EXECUTION,
        error_category=ErrorCategory.TOOL_FAILURE,
        error_text="boom",
    )
    assert CapabilityFallbackStrategy().build_actions(ctx) == []


# --- Phase C: execution guard ------------------------------------------


def _mission() -> Mission:
    return Mission(mission_id="m1", user_goal="test")


def test_action_budget_stops_the_mission():
    mission = _mission()
    guard = ExecutionGuard(mission, GuardLimits(max_total_actions=3))
    for index in range(3):
        assert guard.check_action("search_files", {"i": index}).allowed
        guard.record_action("search_files", {"i": index}, made_progress=True)

    verdict = guard.check_action("search_files", {"i": 99})
    assert not verdict.allowed
    assert verdict.stop is GuardStop.ACTION_BUDGET
    assert verdict.reason


def test_identical_action_is_blocked_before_the_budget_runs_out():
    mission = _mission()
    guard = ExecutionGuard(mission, GuardLimits(max_identical_actions=2))
    for _ in range(2):
        assert guard.check_action("create_folder", {"path": "X"}).allowed
        guard.record_action("create_folder", {"path": "X"}, made_progress=True)

    blocked = guard.check_action("create_folder", {"path": "X"})
    assert not blocked.allowed
    assert blocked.stop is GuardStop.REPEATED_ACTION
    # A different argument is still a different action.
    assert guard.check_action("create_folder", {"path": "Y"}).allowed


def test_no_progress_detection_trips_on_repeated_failures():
    mission = _mission()
    guard = ExecutionGuard(mission, GuardLimits(max_actions_without_progress=3))
    for index in range(3):
        guard.record_action("search_files", {"i": index}, made_progress=False)

    verdict = guard.check_budget()
    assert not verdict.allowed
    assert verdict.stop is GuardStop.NO_PROGRESS


def test_progress_resets_the_stall_counter():
    mission = _mission()
    guard = ExecutionGuard(mission, GuardLimits(max_actions_without_progress=3))
    guard.record_action("a", {}, made_progress=False)
    guard.record_action("b", {}, made_progress=False)
    guard.record_action("c", {}, made_progress=True)
    guard.record_action("d", {}, made_progress=False)

    assert guard.check_budget().allowed


def test_recovery_count_is_finally_enforced():
    mission = _mission()
    mission.recovery_count = DEFAULT_LIMITS.max_recovery_attempts
    verdict = ExecutionGuard(mission).check_budget()

    assert not verdict.allowed
    assert verdict.stop is GuardStop.RECOVERY_BUDGET


def test_identical_plan_cannot_be_run_twice():
    mission = _mission()
    guard = ExecutionGuard(mission)
    steps = [MissionStep(step_id="s1", title="t", tool_name="search_files", tool_arguments={"p": "*"})]

    assert guard.check_plan(steps).allowed
    guard.record_plan(steps)

    same_shape = [
        MissionStep(
            step_id="different_id",
            title="different title",
            tool_name="search_files",
            tool_arguments={"p": "*"},
        )
    ]
    verdict = guard.check_plan(same_shape)
    assert not verdict.allowed
    assert verdict.stop is GuardStop.REPEATED_PLAN


def test_a_genuinely_different_plan_is_allowed():
    mission = _mission()
    guard = ExecutionGuard(mission)
    first = [MissionStep(step_id="s1", title="t", tool_name="search_files", tool_arguments={"p": "*"})]
    guard.record_plan(first)

    second = [MissionStep(step_id="s1", title="t", tool_name="list_directory", tool_arguments={"p": "*"})]
    assert guard.check_plan(second).allowed


def test_guard_state_survives_a_round_trip_through_the_mission():
    mission = _mission()
    ExecutionGuard(mission).record_action("search_files", {"p": "*"}, made_progress=True)

    restored = Mission.from_dict(mission.to_dict())
    guard = ExecutionGuard(restored)

    assert guard.state["action_count"] == 1
    assert guard.check_action("search_files", {"p": "*"}).allowed


def test_fingerprints_ignore_cosmetics_but_not_substance():
    a = MissionStep(step_id="x", title="A", tool_name="copy_file", tool_arguments={"src": "1"})
    b = MissionStep(step_id="y", title="B", tool_name="copy_file", tool_arguments={"src": "1"})
    c = MissionStep(step_id="x", title="A", tool_name="copy_file", tool_arguments={"src": "2"})

    assert plan_fingerprint([a]) == plan_fingerprint([b])
    assert plan_fingerprint([a]) != plan_fingerprint([c])
    assert action_fingerprint("t", {"a": 1}) == action_fingerprint("t", {"a": 1})
    assert action_fingerprint("t", {"a": 1}) != action_fingerprint("t", {"a": 2})
