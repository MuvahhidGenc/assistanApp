"""Phase F2 â€” an understood goal becomes something the existing engine runs.

What matters here is that tool names are chosen locally from the capability
registry, that a declared skill wins when it covers the goal, and that a
capability we cannot supply arguments for becomes a question rather than a
guess.
"""
from __future__ import annotations

import pytest

from hermes.context.conversational_context import ConversationalContext
from hermes.context.entity_decision import Confidence
from hermes.intent.models import AgentIntent
from hermes.intent.router import (
    IntentRouter,
    RouteKind,
    build_input_pool,
    suggest_alternatives,
)
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.skills.executor import SkillExecutor
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.fixture
def skills(registry, tmp_path):
    executor = ToolExecutor(
        registry,
        PolicyEngine([], registry=registry),
        AuditLogger(tmp_path / "audit.log"),
        ApprovalManager(),
    )
    return SkillExecutor(registry, executor)


@pytest.fixture
def router(registry, skills):
    return IntentRouter(registry, skills)


def _intent(**overrides) -> AgentIntent:
    data = {
        "goal": "Bir sey yap",
        "required_capabilities": ["filesystem.write"],
        "references": {},
        "confidence": 0.9,
        "expected_outcome": "Dosya olusur",
    }
    data.update(overrides)
    return AgentIntent.from_dict(data)


# --- input pool -------------------------------------------------------


def test_model_references_are_used_as_arguments():
    intent = _intent(references={"path": "C:/a.txt", "content": "merhaba"})

    pool = build_input_pool(intent)

    assert pool["path"] == "C:/a.txt"
    assert pool["content"] == "merhaba"


def test_session_state_fills_an_argument_the_user_did_not_repeat(tmp_path):
    context = ConversationalContext()
    path = str(tmp_path / "onceki.txt")
    context.last_created_file = path
    context.commit_focus("file", path, source="test")

    pool = build_input_pool(_intent(), context)

    assert pool["path"] == path


def test_what_the_user_just_said_beats_session_state(tmp_path):
    context = ConversationalContext()
    context.last_created_file = str(tmp_path / "eski.txt")
    intent = _intent(references={"path": "C:/yeni.txt"})

    assert build_input_pool(intent, context)["path"] == "C:/yeni.txt"


def test_planned_step_inputs_are_used_by_that_step(router):
    """Inputs belong to the step that declared them, not a shared pool.

    Dumping every planned input into one pool made filesystem.search's folder
    look like filesystem.open's file. The write step below must still receive
    its own path and content.
    """
    intent = AgentIntent.from_dict(
        {
            "goal": "dosya yaz",
            "plan": [
                {"capability": "filesystem.write",
                 "inputs": {"path": "C:/a.txt", "content": "merhaba"}}
            ],
            "confidence": 0.9,
        }
    )

    pool = build_input_pool(intent)
    steps, unfillable = router._build_capability_steps(intent, pool)

    assert unfillable == ()
    assert steps[0].tool_name in {"write_file", "create_word_document"}
    assert steps[0].tool_arguments["path"] == "C:/a.txt"
    assert steps[0].tool_arguments["content"] == "merhaba"


def test_a_plan_implies_the_capability_summary():
    intent = AgentIntent.from_dict(
        {
            "goal": "siteye git ve oku",
            "plan": [
                {"capability": "browser.navigate", "inputs": {"url": "http://x"}},
                {"capability": "browser.read", "inputs": {}},
            ],
            "confidence": 0.9,
        }
    )

    assert intent.required_capabilities == ("browser.navigate", "browser.read")


# --- capability to tool, chosen locally -------------------------------


def test_the_router_picks_the_tool_the_model_never_named(router):
    intent = _intent(
        required_capabilities=["filesystem.list"], references={"path": "C:/klasor"}
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.CAPABILITY_PLAN
    assert plan.steps[0].tool_name == "list_directory"
    assert plan.steps[0].metadata["capability"] == "filesystem.list"


def test_steps_are_chained_in_the_stated_order(router):
    intent = _intent(
        required_capabilities=["browser.navigate", "browser.read"],
        references={"url": "http://ornek"},
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert [step.metadata["capability"] for step in plan.steps] == [
        "browser.navigate",
        "browser.read",
    ]
    assert plan.steps[1].depends_on == [plan.steps[0].step_id]


def test_a_capability_we_cannot_supply_arguments_for_is_not_guessed_at(router):
    """A required capability without inputs is a question, not a partial plan.

    The previous assertion locked a bug: filesystem.list ran while
    browser.navigate was dropped, and the route stayed CAPABILITY_PLAN. That
    let a later mission complete after listing a folder even though navigate
    never happened. Guessing a URL is still forbidden; so is silently
    dropping the required step.
    """
    intent = _intent(
        required_capabilities=["filesystem.list", "browser.navigate"],
        references={"path": "C:/klasor"},
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.QUESTION
    assert plan.unfillable_capabilities == ("browser.navigate",)
    assert plan.steps == []
    assert not plan.is_executable
    assert "http" not in (plan.question or "").casefold()


def test_search_then_open_binds_prior_output_without_guessing_a_path(router):
    intent = AgentIntent.from_dict(
        {
            "goal": "indirmelerde raporu bul ve ac",
            "plan": [
                {
                    "capability": "filesystem.search",
                    "inputs": {"path": "C:/Downloads", "pattern": "*rapor*"},
                },
                {"capability": "filesystem.open", "inputs": {}},
            ],
            "confidence": 0.9,
        }
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.CAPABILITY_PLAN
    assert [step.metadata["capability"] for step in plan.steps] == [
        "filesystem.search",
        "filesystem.open",
    ]
    assert plan.steps[1].tool_name == "open_path"
    assert not plan.steps[1].tool_arguments.get("path")
    assert plan.steps[1].argument_bindings == [
        {
            "argument": "path",
            "source_step_id": plan.steps[0].step_id,
            "source_field": "path",
        }
    ]


def test_a_state_changing_step_with_nothing_to_act_on_is_refused(router):
    """control_service declares an empty schema but really needs a service."""
    intent = AgentIntent.from_dict(
        {
            "goal": "yazici servisini yeniden baslat",
            "plan": [{"capability": "service.manage", "inputs": {}}],
            "confidence": 0.9,
        }
    )

    plan = router.route(intent, confidence=Confidence.RISKY)

    assert plan.kind == RouteKind.QUESTION
    assert plan.unfillable_capabilities == ("service.manage",)


def test_a_read_only_step_may_still_run_with_no_arguments(router):
    """Reading the screen genuinely needs nothing."""
    intent = AgentIntent.from_dict(
        {
            "goal": "ekranda ne yaziyor",
            "plan": [{"capability": "screen.read", "inputs": {}}],
            "confidence": 0.9,
        }
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.CAPABILITY_PLAN
    assert plan.steps[0].tool_name == "read_screen_text"


def _shell_plan(*commands, extra=()):
    plan = [
        {"capability": "terminal.execute", "inputs": {"command": c}} for c in commands
    ]
    plan.extend(extra)
    return AgentIntent.from_dict(
        {"goal": "bir sey yap", "plan": plan, "confidence": 0.9}
    )


def test_one_command_the_user_asked_for_is_allowed(router):
    plan = router.route(_shell_plan("ipconfig /all"), confidence=Confidence.RISKY)

    assert plan.kind == RouteKind.CAPABILITY_PLAN
    assert plan.steps[0].tool_name == "run_command"


def test_a_pile_of_commands_standing_in_for_a_capability_is_refused(router):
    """Live output answered "fix my printer" with five run_command steps."""
    plan = router.route(
        _shell_plan("Get-Service Spooler", "Restart-Service Spooler", "Get-Printer"),
        confidence=Confidence.RISKY,
    )

    assert plan.kind == RouteKind.QUESTION
    assert "terminal.execute" in plan.unfillable_capabilities


def test_a_command_alongside_a_capability_we_lack_is_refused(router):
    """Shelling out to cover a gap hides the gap instead of reporting it."""
    intent = _shell_plan(
        "Restart-Service Spooler",
        extra=[{"capability": "service.manage", "inputs": {}}],
    )

    plan = router.route(intent, confidence=Confidence.RISKY)

    assert plan.kind == RouteKind.QUESTION
    assert "terminal.execute" in plan.unfillable_capabilities
    assert "service.manage" in plan.unfillable_capabilities


def test_the_shell_guard_ignores_wording_entirely(router):
    """Same structure, opposite phrasing, same verdict."""
    verdicts = {
        router.route(
            AgentIntent.from_dict(
                {
                    "goal": goal,
                    "plan": [
                        {"capability": "terminal.execute", "inputs": {"command": "a"}},
                        {"capability": "terminal.execute", "inputs": {"command": "b"}},
                    ],
                    "confidence": 0.9,
                }
            ),
            confidence=Confidence.RISKY,
        ).kind
        for goal in ("terminal komutu calistir", "yazicimi tamir et")
    }

    assert verdicts == {RouteKind.QUESTION}


def test_a_step_carries_the_real_risk_of_its_capability(router, registry):
    intent = _intent(
        required_capabilities=["filesystem.delete"], references={"path": "C:/a.txt"}
    )

    plan = router.route(intent, confidence=Confidence.RISKY)

    assert plan.steps[0].risk_level == "high_risk"
    assert plan.needs_approval is True


# --- skills win when they cover the goal ------------------------------


def test_a_skill_is_preferred_when_it_covers_the_goal(router):
    intent = _intent(
        required_capabilities=["browser.navigate", "browser.read", "filesystem.write"],
        references={"url": "http://ornek", "path": "C:/r.txt"},
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.SKILL
    assert plan.skill_id == "browser_page_report"


def test_skill_matching_ignores_wording_entirely(router):
    """Two different sentences with the same capabilities reach the same skill."""
    caps = ["browser.navigate", "browser.read", "filesystem.write"]
    refs = {"url": "http://ornek", "path": "C:/r.txt"}

    first = router.route(
        AgentIntent.from_dict(
            {"goal": "sayfayi kaydet", "required_capabilities": caps,
             "references": refs, "confidence": 0.9}
        ),
        confidence=Confidence.HIGH,
    )
    second = router.route(
        AgentIntent.from_dict(
            {"goal": "tamamen farkli bir cumle", "required_capabilities": caps,
             "references": refs, "confidence": 0.9}
        ),
        confidence=Confidence.HIGH,
    )

    assert first.skill_id == second.skill_id == "browser_page_report"


def test_a_skill_needing_more_than_the_goal_asks_for_is_not_used(router):
    intent = _intent(required_capabilities=["browser.navigate"], references={"url": "http://x"})

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.CAPABILITY_PLAN


def test_the_skill_receives_the_resolved_inputs(router):
    intent = _intent(
        required_capabilities=["filesystem.write"],
        references={"path": "C:/a.txt", "content": "merhaba"},
    )
    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.SKILL
    assert plan.skill_id == "file_write_verify"
    assert plan.skill_inputs["content"] == "merhaba"


# --- asking instead of guessing ---------------------------------------


def test_a_medium_confidence_goal_still_asks_when_it_says_it_needs_input(router):
    intent = AgentIntent.from_dict(
        {
            "goal": "siteye git",
            "plan": [{"capability": "browser.navigate", "inputs": {}}],
            "needs_user_input": True,
            "clarifying_question": "Hangi adrese gideyim?",
            "confidence": 0.6,
        }
    )

    plan = router.route(intent, confidence=Confidence.MEDIUM)

    assert plan.kind == RouteKind.QUESTION
    assert "adres" in plan.question.casefold() or "Hangi" in plan.question
    intent = _intent(
        confidence=0.1, clarifying_question="Hangi dosyayi kastediyorsun?"
    )

    plan = router.route(intent, confidence=Confidence.LOW)

    assert plan.kind == RouteKind.QUESTION
    assert plan.question == "Hangi dosyayi kastediyorsun?"
    assert not plan.is_executable


def test_a_goal_with_nothing_to_act_on_becomes_a_question(router):
    """Confident about the goal, but no argument anywhere to run it with.

    This also pins that a skill is not started when its first step could
    never be called.
    """
    intent = _intent(required_capabilities=["filesystem.write"], references={})

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.QUESTION
    assert plan.question


def test_a_low_confidence_goal_without_a_question_still_asks_something(router):
    plan = router.route(_intent(confidence=0.1), confidence=Confidence.LOW)

    assert plan.question


# --- honest capability gaps -------------------------------------------


def test_a_capability_this_machine_lacks_is_reported_with_alternatives(registry, skills):
    router = IntentRouter(registry, skills)
    intent = _intent(required_capabilities=["filesystem.encrypt"])

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.UNSUPPORTED
    assert plan.unavailable_capabilities == ("filesystem.encrypt",)
    assert plan.alternatives["filesystem.encrypt"]
    assert not plan.is_executable


def test_alternatives_come_from_the_same_domain(registry):
    suggestions = suggest_alternatives(registry, "filesystem.encrypt")

    assert suggestions
    assert all(name.startswith("filesystem.") for name in suggestions)


def test_alternatives_are_empty_for_an_unknown_domain(registry):
    assert suggest_alternatives(registry, "telepathy.read") == ()


# --- conversation -----------------------------------------------------


def test_a_conversation_does_not_select_tools(router):
    intent = AgentIntent.from_dict(
        {
            "goal": "selamlasma",
            "mode": "conversation",
            "reply": "Merhaba, nasil yardimci olabilirim?",
            "confidence": 0.9,
        }
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.CONVERSATION
    assert not plan.steps
    assert not plan.is_executable
    assert "Merhaba" in plan.question


# --- no skills configured ---------------------------------------------


def test_routing_still_works_without_any_skills(registry):
    router = IntentRouter(registry, skills=None)
    intent = _intent(references={"path": "C:/a.txt", "content": "x"})

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.CAPABILITY_PLAN
    assert plan.steps[0].tool_name == "write_file"


def test_novel_multi_step_capabilities_are_bound_locally_not_by_wording(router):
    """Architecture criterion: a new sentence is just capabilities plus inputs."""
    intent = AgentIntent.from_dict(
        {
            "goal": "tarayiciyi ac, siteye git, oku, belgeye yaz",
            "plan": [
                {"capability": "browser.open", "inputs": {"app": "chrome"}},
                {"capability": "browser.navigate", "inputs": {"url": "https://openai.com"}},
                {"capability": "browser.read", "inputs": {}},
                {
                    "capability": "document.create",
                    "inputs": {"path": "C:/ozet.docx", "content": "ozet"},
                },
            ],
            "confidence": 0.9,
        }
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.is_executable
    tools = [step.tool_name for step in plan.steps]
    assert "open_url" in tools or "open_app" in tools
    assert "read_screen_text" in tools
    assert "write_file" in tools or "create_word_document" in tools
    assert all(step.metadata["source"] == "intent_router" for step in plan.steps)


def test_a_follow_up_uses_session_file_without_inventing_a_path(router, tmp_path):
    context = ConversationalContext()
    created = str(tmp_path / "onceki.docx")
    context.last_created_file = created
    context.commit_focus("file", created, source="test")

    intent = AgentIntent.from_dict(
        {
            "goal": "son olusturulan dosyayi ac",
            "plan": [{"capability": "filesystem.open", "inputs": {}}],
            "confidence": 0.9,
        }
    )

    plan = router.route(intent, confidence=Confidence.HIGH, context=context)

    assert plan.kind == RouteKind.CAPABILITY_PLAN
    assert plan.steps[0].tool_name == "open_path"
    assert plan.steps[0].tool_arguments["path"] == created


def test_open_without_any_target_asks_instead_of_guessing(router):
    intent = AgentIntent.from_dict(
        {
            "goal": "sunu ac",
            "plan": [{"capability": "filesystem.open", "inputs": {}}],
            "confidence": 0.9,
        }
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.QUESTION
    assert not plan.steps

