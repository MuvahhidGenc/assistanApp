"""Phase E â€” skills as executable procedures, not planner hints.

The point of these tests is not that two YAML files run. It is that a
procedure can be described without naming a tool, and still be resolved,
executed, verified, recovered and bounded by the machinery built in the
earlier phases.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from hermes.config.settings import RiskLevel
from hermes.mission.execution_guard import GuardLimits, GuardStop
from hermes.mission.models import Mission, MissionStep, MissionStepStatus, StepAction
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.skills.executor import SkillExecutor
from hermes.skills.loader import load_all_skills, load_executable_skills, match_skills_for_goal
from hermes.skills.models import Skill, resolve_value, validate_skill
from hermes.tools.base import ToolExecutionResult
from hermes.tools.capabilities import select_tool_for_capability
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.fixture
def tool_executor(registry, tmp_path):
    """Gated on high risk only, so file writes run and the tests stay on topic.

    The approval test below deliberately uses the stricter default policy.
    """
    return ToolExecutor(
        registry,
        PolicyEngine([RiskLevel.HIGH_RISK], registry=registry),
        AuditLogger(tmp_path / "audit.log"),
        ApprovalManager(),
    )


@pytest.fixture
def skills(registry, tool_executor):
    return SkillExecutor(registry, tool_executor)


def _skill(**overrides) -> Skill:
    data = {
        "id": "probe",
        "title": "Probe",
        "steps": [
            {
                "id": "write",
                "capability": "filesystem.write",
                "inputs": {"path": "{{ inputs.path }}", "content": "{{ inputs.content }}"},
                "output": "path",
            }
        ],
    }
    data.update(overrides)
    return Skill.from_dict(data)


# --- loading and validation -------------------------------------------


def test_shipped_skills_load_and_validate(registry):
    valid, rejected = load_executable_skills(set(registry.capabilities()))

    assert rejected == {}
    assert "file_write_verify" in valid
    assert "browser_page_report" in valid


def test_a_skill_naming_an_unknown_capability_is_rejected(registry):
    skill = _skill(steps=[{"id": "a", "capability": "telepathy.read"}])

    result = validate_skill(skill, set(registry.capabilities()))

    assert not result.ok
    assert any("telepathy.read" in error for error in result.errors)


def test_a_skill_without_steps_is_rejected(registry):
    result = validate_skill(_skill(steps=[]), set(registry.capabilities()))

    assert not result.ok


def test_duplicate_step_ids_are_rejected(registry):
    skill = _skill(
        steps=[
            {"id": "same", "capability": "filesystem.list"},
            {"id": "same", "capability": "filesystem.list"},
        ]
    )

    assert not validate_skill(skill, set(registry.capabilities())).ok


def test_hint_only_skills_still_load_for_the_planner():
    """The pre-Phase-E consumers must keep working."""
    hints = load_all_skills()

    assert any(hint.skill_id == "desktop_organize" for hint in hints)
    assert match_skills_for_goal("Masaüstümü biraz düzenle.")


# --- capability resolution, not tool names ----------------------------


def test_no_shipped_skill_names_a_tool(registry):
    """A skill that names a tool is a skill that breaks when tools change."""
    tool_names = {definition.name for definition in registry.list_tools()}
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/hermes/skills/procedures").glob("*.yaml")
    )

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("capability:"):
            continue
        assert stripped.split(":", 1)[1].strip() not in tool_names


def test_capability_resolves_to_the_tool_that_uses_most_of_the_input(registry):
    """create_folder also offers filesystem.write but ignores the content."""
    with_content = select_tool_for_capability(
        registry, "filesystem.write", {"path": "a.txt", "content": "x"}
    )
    without_content = select_tool_for_capability(
        registry, "filesystem.write", {"path": "a"}
    )

    assert with_content.tool_name == "write_file"
    assert without_content.tool_name == "create_folder"


def test_a_filename_is_not_satisfied_by_creating_a_folder(registry):
    """Live: filesystem.write + notlar.txt selected create_folder."""
    assert select_tool_for_capability(
        registry, "filesystem.write", {"path": "notlar.txt"}
    ) is None


def test_an_empty_required_argument_does_not_count_as_filled(registry):
    assert select_tool_for_capability(
        registry, "browser.navigate", {"url": ""}
    ) is None


@pytest.mark.asyncio
async def test_missing_capability_reports_the_capability_not_a_crash(
    registry, tool_executor
):
    skill = _skill(steps=[{"id": "a", "capability": "time.travel"}])
    executor = SkillExecutor(registry, tool_executor, skills={"probe": skill})

    outcome = await executor.execute("probe")

    assert outcome.success is False
    assert "time.travel" in outcome.summary


# --- execution ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_simple_skill_writes_and_proves_the_file_exists(skills, tmp_path):
    target = tmp_path / "rapor.txt"

    outcome = await skills.execute(
        "file_write_verify", inputs={"path": str(target), "content": "merhaba"}
    )

    assert outcome.success is True
    assert target.read_text(encoding="utf-8") == "merhaba"
    assert outcome.steps[0].tool_name == "write_file"
    assert outcome.outputs["path"]


@pytest.mark.asyncio
async def test_a_skill_does_not_report_success_when_the_file_never_appeared(
    skills, tmp_path, registry
):
    """The tool lies; the skill's own criterion must still catch it."""
    target = tmp_path / "ghost.txt"
    registry.get("write_file").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"path": str(target)})
    )

    outcome = await skills.execute(
        "file_write_verify", inputs={"path": str(target), "content": "x"}
    )

    assert outcome.success is False
    assert not target.exists()


@pytest.mark.asyncio
async def test_step_output_flows_into_the_next_step(skills, tmp_path, registry):
    """browser.read output becomes the report's content without any glue code."""
    page_text = "Hermes ornek sayfasi. Bu metin raporun icerigi olacak."
    report = tmp_path / "sayfa.txt"
    registry.get("open_url").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"url": "http://ornek"})
    )
    registry.get("read_screen_text").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"text": page_text})
    )

    outcome = await skills.execute(
        "browser_page_report",
        inputs={"url": "http://ornek", "path": str(report)},
    )

    assert outcome.success is True
    assert report.read_text(encoding="utf-8") == page_text
    assert [step.tool_name for step in outcome.steps] == [
        "open_url",
        "read_screen_text",
        "write_file",
    ]


@pytest.mark.asyncio
async def test_an_empty_page_fails_before_a_useless_report_is_written(
    skills, tmp_path, registry
):
    report = tmp_path / "sayfa.txt"
    registry.get("open_url").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"url": "http://ornek"})
    )
    registry.get("read_screen_text").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"text": ""})
    )

    outcome = await skills.execute(
        "browser_page_report",
        inputs={"url": "http://ornek", "path": str(report)},
    )

    assert outcome.success is False
    assert not report.exists()


# --- dynamic input -----------------------------------------------------


def test_a_whole_string_reference_keeps_structured_output():
    scope = {"steps": {"read": {"output": {"rows": [1, 2]}}}}

    assert resolve_value("{{ steps.read.output }}", scope) == {"rows": [1, 2]}


def test_an_embedded_reference_is_interpolated():
    scope = {"inputs": {"name": "rapor"}}

    assert resolve_value("{{ inputs.name }}.txt", scope) == "rapor.txt"


def test_context_is_readable_from_a_skill(tmp_path):
    from hermes.context.conversational_context import ConversationalContext

    context = ConversationalContext()
    context.last_created_file = str(tmp_path / "onceki.txt")
    from hermes.skills.executor import _context_scope

    scope = {"context": _context_scope(context)}

    assert resolve_value("{{ context.last_created_file }}", scope) == context.last_created_file


# --- verification through observation ----------------------------------


@pytest.mark.asyncio
async def test_a_step_can_be_judged_by_re_reading_the_world(
    registry, tool_executor, tmp_path
):
    """"App opened" is decided by what windows exist, not by the launcher."""
    registry.get("open_app").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"app": "Word"})
    )
    registry.get("list_windows").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output="Belge1 - Word")
    )
    skill = _skill(
        steps=[
            {
                "id": "open",
                "capability": "application.open",
                "inputs": {"app": "winword"},
                "success_criteria": {
                    "observe_capability": "window.inspect",
                    "output_contains": "Word",
                },
            }
        ]
    )
    executor = SkillExecutor(registry, tool_executor, skills={"probe": skill})

    outcome = await executor.execute("probe")

    assert outcome.success is True


@pytest.mark.asyncio
async def test_observation_that_disagrees_fails_the_step(
    registry, tool_executor
):
    registry.get("open_app").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"app": "Word"})
    )
    registry.get("list_windows").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output="Sadece masaustu")
    )
    skill = _skill(
        steps=[
            {
                "id": "open",
                "capability": "application.open",
                "inputs": {"app": "winword"},
                "success_criteria": {
                    "observe_capability": "window.inspect",
                    "output_contains": "Word",
                },
            }
        ]
    )
    executor = SkillExecutor(registry, tool_executor, skills={"probe": skill})

    outcome = await executor.execute("probe")

    assert outcome.success is False
    assert "Word" in outcome.summary


# --- risk and approval -------------------------------------------------


@pytest.mark.asyncio
async def test_a_skill_calling_itself_low_risk_still_needs_approval(
    registry, tmp_path
):
    """Skill metadata must never outrank the policy engine."""
    approvals = ApprovalManager()
    executor = ToolExecutor(
        registry,
        PolicyEngine([], registry=registry),
        AuditLogger(tmp_path / "audit.log"),
        approvals,
    )
    victim = tmp_path / "silinecek.txt"
    victim.write_text("veri", encoding="utf-8")
    skill = _skill(
        risk="low",
        steps=[
            {
                "id": "wipe",
                "capability": "filesystem.delete",
                "inputs": {"path": "{{ inputs.path }}"},
            }
        ],
    )
    skills = SkillExecutor(registry, executor, skills={"probe": skill})

    outcome = await skills.execute("probe", inputs={"path": str(victim)})

    assert outcome.success is False
    assert outcome.requires_approval is True
    assert victim.exists()


def test_a_caller_supplied_risk_level_cannot_lower_a_tools_real_risk(registry):
    """ToolCallRequest defaults to read_only, which used to open the gate."""
    from hermes.security.policy_engine import PolicyDecision
    from hermes.server.models import ToolCallRequest

    policy = PolicyEngine([], registry=registry)
    call = ToolCallRequest(name="delete_path", arguments={"path": "x"})

    assert call.risk_level == "read_only"
    result = policy.evaluate("delete_path", call.arguments, server_risk_level=call.risk_level)

    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.risk_level == RiskLevel.HIGH_RISK


def test_a_caller_supplied_risk_level_can_still_raise_the_bar(registry):
    from hermes.security.policy_engine import PolicyDecision

    policy = PolicyEngine([], registry=registry)

    result = policy.evaluate("list_directory", {"path": "x"}, server_risk_level="high_risk")

    assert result.decision == PolicyDecision.REQUIRE_APPROVAL


# --- recovery ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_step_goes_through_recovery_not_a_blind_retry(
    registry, tool_executor, tmp_path, monkeypatch
):
    seen: list[str] = []
    real = registry.get("write_file").execute

    async def flaky(**kwargs):
        seen.append(kwargs.get("path", ""))
        if len(seen) == 1:
            return ToolExecutionResult(success=False, error="gecici hata")
        return await real(**kwargs)

    registry.get("write_file").execute = flaky
    target = tmp_path / "sonunda.txt"

    outcome = await SkillExecutor(registry, tool_executor).execute(
        "file_write_verify", inputs={"path": str(target), "content": "veri"}
    )

    assert len(seen) > 1
    if outcome.success:
        assert outcome.steps[0].recovered is True
        assert target.exists()
    else:
        assert "sonunda" in outcome.summary or outcome.summary


@pytest.mark.asyncio
async def test_a_permanently_failing_step_is_reported_not_papered_over(
    registry, tool_executor, tmp_path
):
    registry.get("write_file").execute = AsyncMock(
        return_value=ToolExecutionResult(success=False, error="disk dolu")
    )
    target = tmp_path / "olmayacak.txt"

    outcome = await SkillExecutor(registry, tool_executor).execute(
        "file_write_verify", inputs={"path": str(target), "content": "veri"}
    )

    assert outcome.success is False
    assert not target.exists()
    assert outcome.summary


# --- composition and guards -------------------------------------------


@pytest.mark.asyncio
async def test_a_skill_can_call_another_skill(registry, tool_executor, tmp_path):
    target = tmp_path / "ic_ice.txt"
    outer = Skill.from_dict(
        {
            "id": "outer",
            "steps": [
                {
                    "id": "delegate",
                    "skill": "file_write_verify",
                    "inputs": {
                        "path": "{{ inputs.path }}",
                        "content": "{{ inputs.content }}",
                    },
                    "output": "inner",
                }
            ],
        }
    )
    valid, _ = load_executable_skills(set(registry.capabilities()))
    valid["outer"] = outer
    skills = SkillExecutor(registry, tool_executor, skills=valid)

    outcome = await skills.execute(
        "outer", inputs={"path": str(target), "content": "derin"}
    )

    assert outcome.success is True
    assert target.read_text(encoding="utf-8") == "derin"


@pytest.mark.asyncio
async def test_a_self_referencing_skill_cannot_recurse_forever(
    registry, tool_executor
):
    loop = Skill.from_dict(
        {"id": "loop", "steps": [{"id": "again", "skill": "loop"}]}
    )
    skills = SkillExecutor(registry, tool_executor, skills={"loop": loop})

    outcome = await skills.execute("loop")

    assert outcome.success is False
    assert outcome.guard_stop in {GuardStop.SKILL_DEPTH, GuardStop.REPEATED_SKILL}


@pytest.mark.asyncio
async def test_the_same_skill_with_the_same_input_is_stopped_by_the_guard(
    registry, tool_executor, tmp_path
):
    """Composition is fine; running the identical procedure forever is not."""
    mission = Mission(mission_id="m1", user_goal="tekrar")
    skills = SkillExecutor(registry, tool_executor)
    inputs = {"path": str(tmp_path / "a.txt"), "content": "x"}

    stops = []
    for _ in range(5):
        outcome = await skills.execute(
            "file_write_verify", inputs=dict(inputs), mission=mission
        )
        stops.append(outcome.guard_stop)

    assert GuardStop.REPEATED_SKILL in stops


@pytest.mark.asyncio
async def test_the_action_budget_applies_to_skill_steps(
    registry, tool_executor, tmp_path
):
    mission = Mission(mission_id="m2", user_goal="butce")
    skills = SkillExecutor(
        registry, tool_executor, guard_limits=GuardLimits(max_total_actions=1)
    )

    first = await skills.execute(
        "file_write_verify",
        inputs={"path": str(tmp_path / "1.txt"), "content": "a"},
        mission=mission,
    )
    second = await skills.execute(
        "file_write_verify",
        inputs={"path": str(tmp_path / "2.txt"), "content": "b"},
        mission=mission,
    )

    assert first.success is True
    assert second.success is False
    assert second.guard_stop


# --- legacy handlers ---------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_logical_handlers_run_untouched_when_no_skill_claims_them(
    registry, tool_executor, tmp_path
):
    """The delegation hook must be inert by default."""
    from hermes.mission.compound_goal import LOGICAL_KIND_READ_VERIFY_FILES
    from hermes.mission.engine import MissionEngine, _StepRunOutcome
    from hermes.mission.store import MissionStore

    target = tmp_path / "mevcut.txt"
    target.write_text("beklenen", encoding="utf-8")
    engine = MissionEngine(MissionStore(tmp_path / "missions"), registry, tool_executor)
    mission = Mission(mission_id="legacy", user_goal="oku")
    step = MissionStep(
        step_id="s1",
        title="Oku",
        action=StepAction.LOGICAL,
        metadata={
            "logical_kind": LOGICAL_KIND_READ_VERIFY_FILES,
            "file_path": str(target),
            "expected_content": "beklenen",
        },
    )
    mission.steps.append(step)

    await engine._run_logical_step(mission, step, _StepRunOutcome())

    assert step.status == MissionStepStatus.COMPLETED
    assert step.verification_method == "compound_read_verify"


@pytest.mark.asyncio
async def test_a_skill_can_take_over_a_legacy_logical_kind(
    registry, tool_executor, tmp_path
):
    """This is the migration path: claim the kind, delete the handler later."""
    from hermes.mission.engine import MissionEngine, _StepRunOutcome
    from hermes.mission.store import MissionStore

    target = tmp_path / "skill_yazdi.txt"
    claiming = Skill.from_dict(
        {
            "id": "claiming",
            "title": "Devralan",
            "legacy_logical_kind": "demo_kind",
            "steps": [
                {
                    "id": "write",
                    "capability": "filesystem.write",
                    "inputs": {
                        "path": "{{ inputs.path }}",
                        "content": "{{ inputs.content }}",
                    },
                }
            ],
        }
    )
    skills = SkillExecutor(registry, tool_executor, skills={"claiming": claiming})
    engine = MissionEngine(
        MissionStore(tmp_path / "missions"),
        registry,
        tool_executor,
        skill_executor=skills,
    )
    mission = Mission(mission_id="claim", user_goal="devral")
    step = MissionStep(
        step_id="s1",
        title="Devralinan adim",
        action=StepAction.LOGICAL,
        metadata={
            "logical_kind": "demo_kind",
            "skill_inputs": {"path": str(target), "content": "skill"},
        },
    )
    mission.steps.append(step)

    outcome = await engine._run_logical_step(mission, step, _StepRunOutcome())

    assert outcome.step_done is True
    assert step.verification_method == "skill:claiming"
    assert target.read_text(encoding="utf-8") == "skill"


# --- Phase F readiness -------------------------------------------------


def test_skills_expose_semantic_hints_rather_than_match_patterns(registry):
    """Phase F will match on meaning; nothing here routes by substring."""
    valid, _ = load_executable_skills(set(registry.capabilities()))

    report = valid["browser_page_report"]
    assert report.intent_hints
    assert report.description
    assert report.required_capabilities


def test_the_frozen_build_ships_the_skill_definitions():
    """A green build with no skills inside it is the failure mode to avoid.

    The loader looks beside its own module, so the bundle destination has to
    match that relative path exactly.
    """
    from hermes.skills import loader

    expected = "/".join(Path(loader.__file__).resolve().parts[-3:-1] + ("procedures",))
    spec = Path("hermes-client.spec").read_text(encoding="utf-8")

    assert expected == "hermes/skills/procedures"
    assert expected in spec


def test_runnable_skills_are_filtered_by_available_capabilities(skills):
    runnable = {skill.skill_id for skill in skills.runnable_skills()}

    assert "file_write_verify" in runnable

