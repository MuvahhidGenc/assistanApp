"""Phase F — understanding sits in front of the keyword wall.

The intent/router units already exist. These tests pin the production
call-chain: a goal nobody listed in an allowlist still reaches structured
understanding, a complete single local action still skips it, and a
follow-up with session context is not treated as a brand-new unknown task.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.intent.models import AgentIntent
from hermes.server.models import Session


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings.load()


def _orchestrator(settings, *, chat_payload: dict | None = None):
    server = MagicMock()
    server.create_session = AsyncMock(return_value=Session(id="sess-1"))
    server.create_run = AsyncMock()
    server.update_session = AsyncMock(return_value=Session(id="sess-1"))
    if chat_payload is None:
        server.chat = AsyncMock(side_effect=RuntimeError("understanding offline"))
    else:
        server.chat = AsyncMock(
            return_value={"choices": [{"message": {"content": json.dumps(chat_payload)}}]}
        )
    return AgentOrchestrator(settings, server), server


# --- allowlist is no longer the gate ----------------------------------


@pytest.mark.asyncio
async def test_an_allowlisted_sentence_still_goes_through_understanding_first(
    settings,
):
    orch, server = _orchestrator(settings)
    orch._handle_with_intent = AsyncMock(return_value="yaziciyi inceliyorum")  # noqa: SLF001

    result = await orch.process_message(
        "Yazicim calismiyor, nedenini bul ve mumkunse duzelt."
    )

    orch._handle_with_intent.assert_awaited()  # noqa: SLF001
    assert result == "yaziciyi inceliyorum"
    server.create_run.assert_not_called()


@pytest.mark.asyncio
async def test_a_goal_with_no_keyword_match_still_reaches_understanding(settings):
    orch, server = _orchestrator(settings)
    orch._handle_with_intent = AsyncMock(return_value="gereksiz dosyalari ariyorum")  # noqa: SLF001

    result = await orch.process_message(
        "Bilgisayarimda gereksiz dosyalari bul ve duzenle."
    )

    orch._handle_with_intent.assert_awaited()  # noqa: SLF001
    assert "gereksiz" in result
    server.create_run.assert_not_called()


@pytest.mark.asyncio
async def test_a_multi_step_web_and_document_goal_is_not_stolen_by_open_app(
    settings,
):
    """'Chrome'u ac, ... ozetle' used to match only the first verb."""
    orch, server = _orchestrator(settings)
    orch._handle_with_intent = AsyncMock(return_value="cok adimli gorev")  # noqa: SLF001
    orch._execute_resolved_local_intent = AsyncMock(return_value="sadece chrome")  # noqa: SLF001

    result = await orch.process_message(
        "Chrome'u ac, verdigim siteye git, hesabimdaki bilgileri incele ve ozetle."
    )

    orch._handle_with_intent.assert_awaited()  # noqa: SLF001
    orch._execute_resolved_local_intent.assert_not_awaited()  # noqa: SLF001
    assert result == "cok adimli gorev"


# --- fast paths remain for complete single actions --------------------


@pytest.mark.asyncio
async def test_a_complete_single_local_action_still_skips_understanding(settings):
    orch, server = _orchestrator(settings)
    orch._handle_with_intent = AsyncMock(return_value="should not run")  # noqa: SLF001
    orch._execute_local_tool = AsyncMock(  # noqa: SLF001
        return_value=MagicMock(
            success=True, output={"adapter": "Wi-Fi", "servers": ["8.8.8.8"]}, error=None
        )
    )

    result = await orch.process_message("dns degistir google yap")

    orch._handle_with_intent.assert_not_awaited()  # noqa: SLF001
    server.create_run.assert_not_called()
    assert "DNS" in result or "dns" in result.lower() or result


# --- context follow-ups -----------------------------------------------


@pytest.mark.asyncio
async def test_a_deictic_follow_up_is_handed_to_understanding_with_session_state(
    settings,
):
    from hermes.context.conversational_context import ConversationalContext
    from hermes.context.entity_decision import Confidence
    from hermes.intent.models import AgentIntent
    from hermes.intent.understanding import IntentResult, IntentValidation

    context = ConversationalContext()
    context.last_browser_url = "https://ornek.site/makale"
    context.last_application = "chrome"
    orch, server = _orchestrator(settings)
    captured: dict[str, object] = {}

    async def fake_understand(message, *, context=None, history=None):
        captured["message"] = message
        captured["url"] = getattr(context, "last_browser_url", None)
        intent = AgentIntent.from_dict(
            {
                "goal": "acik sayfaya git",
                "needs_user_input": True,
                "clarifying_question": "Hangi siteye gideyim?",
                "confidence": 0.2,
            }
        )
        return IntentResult(
            intent=intent,
            confidence=Confidence.LOW,
            validation=IntentValidation(ok=True),
        )

    with patch(
        "hermes.context.conversational_context.ConversationalContext.load",
        return_value=context,
    ):
        orch._intent_layer()  # noqa: SLF001
        orch._intent_understanding.understand = fake_understand  # noqa: SLF001
        result = await orch.process_message("Su siteye gir.")

    assert captured.get("message") == "Su siteye gir."
    assert captured.get("url") == "https://ornek.site/makale"
    assert "site" in result.lower() or "Site" in result
    server.create_run.assert_not_called()


# --- model output still cannot name tools -----------------------------


def test_routed_steps_never_come_from_a_model_tool_name(settings):
    from hermes.context.entity_decision import Confidence
    from hermes.intent.router import IntentRouter, RouteKind
    from hermes.security.approval_manager import ApprovalManager
    from hermes.security.policy_engine import AuditLogger, PolicyEngine
    from hermes.skills.executor import SkillExecutor
    from hermes.tools.executor import ToolExecutor
    from hermes.tools.registry import create_default_registry

    registry = create_default_registry()
    executor = ToolExecutor(
        registry,
        PolicyEngine([], registry=registry),
        AuditLogger("audit.log"),
        ApprovalManager(),
    )
    router = IntentRouter(registry, SkillExecutor(registry, executor))
    intent = AgentIntent.from_dict(
        {
            "goal": "klasoru listele",
            "plan": [{"capability": "filesystem.list", "inputs": {"path": "C:/x"}}],
            "confidence": 0.9,
        }
    )

    plan = router.route(intent, confidence=Confidence.HIGH)

    assert plan.kind == RouteKind.CAPABILITY_PLAN
    assert plan.steps[0].tool_name == "list_directory"
    assert plan.steps[0].metadata["source"] == "intent_router"


@pytest.mark.asyncio
async def test_an_unknown_capability_does_not_fall_through_to_create_run(settings):
    orch, server = _orchestrator(
        settings,
        chat_payload={
            "goal": "aklini oku",
            "required_capabilities": ["telepathy.read"],
            "mode": "task",
            "confidence": 0.9,
        },
    )

    result = await orch.process_message("Aklimi oku.")

    server.create_run.assert_not_called()
    assert "telepathy.read" in result or "yapamiyorum" in result.casefold()


@pytest.mark.asyncio
async def test_llm_unavailable_uses_legacy_fallback_with_a_notice(settings):
    orch, server = _orchestrator(settings)
    orch._run_legacy_fallback = AsyncMock(return_value="yerel yanit")  # noqa: SLF001

    result = await orch.process_message(
        "Yazicim calismiyor, nedenini bul ve mumkunse duzelt."
    )

    orch._run_legacy_fallback.assert_awaited()  # noqa: SLF001
    assert "Anlama katmanina su an ulasilamadi" in result
    assert "yerel yanit" in result


@pytest.mark.asyncio
async def test_a_conversation_reply_does_not_create_a_run(settings):
    orch, server = _orchestrator(
        settings,
        chat_payload={
            "goal": "selamlasma",
            "mode": "conversation",
            "reply": "Merhaba, nasil yardimci olabilirim?",
            "confidence": 0.95,
        },
    )

    result = await orch.process_message("Merhaba")

    server.create_run.assert_not_called()
    assert "Merhaba" in result
