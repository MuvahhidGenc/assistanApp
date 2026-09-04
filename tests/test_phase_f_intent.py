"""Phase F1 — a user message becomes a structured, capability-shaped goal.

The model has no schema-constrained decoding available here, so malformed and
chatty replies are ordinary cases. These tests pin that, plus the decision
bands and the rule that a model can never talk risk down.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from hermes.config.settings import RiskLevel
from hermes.context.entity_decision import Confidence
from hermes.intent.models import (
    AgentIntent,
    decide_confidence,
    extract_intent_json,
    repair_intent_json,
    validate_intent,
)
from hermes.intent.understanding import IntentUnderstanding, build_context_block
from hermes.tools.capabilities import capability_risk_floor, known_capabilities
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


class FakeClient:
    """Stands in for the server; records what it was asked."""

    def __init__(
        self,
        content: str = "",
        raises: Exception | None = None,
        contents: list[str] | None = None,
    ) -> None:
        self.content = content
        self.contents = list(contents) if contents is not None else None
        self.raises = raises
        self.prompts: list[str] = []

    async def chat(self, request):
        self.prompts.append(request.message)
        if self.raises is not None:
            raise self.raises
        if self.contents is not None:
            if not self.contents:
                return {"choices": [{"message": {"content": self.content}}]}
            return {"choices": [{"message": {"content": self.contents.pop(0)}}]}
        return {"choices": [{"message": {"content": self.content}}]}


def _payload(**overrides):
    data = {
        "goal": "Masaustundeki dosyalari duzenle",
        "subgoals": ["Dosyalari listele", "Turlerine gore ayir"],
        "required_capabilities": ["filesystem.list", "filesystem.move"],
        "constraints": [],
        "references": {},
        "ambiguity": [],
        "needs_user_input": False,
        "clarifying_question": "",
        "confidence": 0.9,
        "expected_outcome": "Dosyalar klasorlere ayrilmis olur",
    }
    data.update(overrides)
    return data


# --- parsing a model reply --------------------------------------------


def test_a_clean_json_reply_is_parsed():
    assert extract_intent_json(json.dumps(_payload()))["goal"]


def test_a_fenced_json_reply_is_parsed():
    raw = "Tabii!\n```json\n" + json.dumps(_payload()) + "\n```\nUmarim yardimci olur."

    assert extract_intent_json(raw)["goal"]


def test_a_reply_wrapped_in_chatter_is_parsed():
    raw = "Iste analiz: " + json.dumps(_payload()) + " Baska bir sey ister misiniz?"

    assert extract_intent_json(raw)["required_capabilities"]


def test_prose_without_json_is_not_guessed_at():
    assert extract_intent_json("Elbette, masaustunu duzenleyebilirim.") is None


def test_malformed_json_is_not_guessed_at():
    assert extract_intent_json('{"goal": "x", "subgoals": [unclosed') is None


def test_trailing_commas_are_repaired_without_inventing_fields():
    raw = '{"goal": "dosya yaz", "required_capabilities": ["filesystem.write"], "confidence": 0.9,}'
    parsed = repair_intent_json(raw)
    assert parsed is not None
    assert parsed["goal"] == "dosya yaz"
    assert parsed["required_capabilities"] == ["filesystem.write"]


def test_prose_is_not_repaired_into_an_intent():
    assert repair_intent_json("Elbette, hemen yapiyorum.") is None


def test_an_empty_reply_is_not_guessed_at():
    assert extract_intent_json("") is None


def test_a_json_array_is_rejected():
    """Only an object matches the schema; a list is not a near-miss to accept."""
    assert extract_intent_json('["filesystem.list"]') is None


# --- schema shaping ---------------------------------------------------


def test_missing_fields_fall_back_to_safe_defaults():
    intent = AgentIntent.from_dict({"goal": "bir sey yap"})

    assert intent.required_capabilities == ()
    assert intent.reported_confidence == 0.0
    assert intent.needs_user_input is False


def test_a_confidence_outside_the_range_is_clamped():
    assert AgentIntent.from_dict({"confidence": 7}).reported_confidence == 1.0
    assert AgentIntent.from_dict({"confidence": -3}).reported_confidence == 0.0


def test_a_non_numeric_confidence_does_not_crash():
    assert AgentIntent.from_dict({"confidence": "cok eminim"}).reported_confidence == 0.0


def test_a_single_capability_string_is_accepted_as_a_list():
    intent = AgentIntent.from_dict({"required_capabilities": "filesystem.list"})

    assert intent.required_capabilities == ("filesystem.list",)


# --- validation -------------------------------------------------------


def test_a_valid_intent_passes(registry):
    intent = AgentIntent.from_dict(_payload())

    result = validate_intent(intent, set(known_capabilities()), set(registry.capabilities()))

    assert result.ok
    assert not result.has_capability_gap


def test_an_invented_capability_is_rejected(registry):
    intent = AgentIntent.from_dict(_payload(required_capabilities=["telepathy.read"]))

    result = validate_intent(intent, set(known_capabilities()), set(registry.capabilities()))

    assert not result.ok
    assert result.unknown_capabilities == ("telepathy.read",)


def test_a_goalless_intent_is_rejected(registry):
    intent = AgentIntent.from_dict(_payload(goal=""))

    assert not validate_intent(intent, set(known_capabilities()), set(registry.capabilities())).ok


def test_a_known_but_unavailable_capability_is_reported_separately():
    """Understood the request, but this machine cannot do it."""
    intent = AgentIntent.from_dict(_payload(required_capabilities=["filesystem.move"]))

    result = validate_intent(intent, set(known_capabilities()), available_capabilities=set())

    assert result.ok
    assert result.unavailable_capabilities == ("filesystem.move",)
    assert result.has_capability_gap


def test_an_intent_with_no_capabilities_is_rejected_unless_it_is_a_question(registry):
    known, available = set(known_capabilities()), set(registry.capabilities())

    silent = AgentIntent.from_dict(_payload(required_capabilities=[]))
    asking = AgentIntent.from_dict(
        _payload(required_capabilities=[], needs_user_input=True)
    )

    assert not validate_intent(silent, known, available).ok
    assert validate_intent(asking, known, available).ok


# --- decision bands ---------------------------------------------------


def test_a_confident_intent_is_high():
    assert decide_confidence(AgentIntent.from_dict(_payload(confidence=0.9))) == Confidence.HIGH


def test_a_partial_intent_is_medium():
    assert decide_confidence(AgentIntent.from_dict(_payload(confidence=0.6))) == Confidence.MEDIUM


def test_a_vague_intent_is_low():
    assert decide_confidence(AgentIntent.from_dict(_payload(confidence=0.1))) == Confidence.LOW


def test_a_destructive_intent_is_risky_however_sure_the_model_sounded(registry):
    """Observed live: filesystem.delete chosen at 0.85 with needs_input false."""
    intent = AgentIntent.from_dict(
        _payload(required_capabilities=["filesystem.delete"], confidence=0.99)
    )
    floor = capability_risk_floor(registry, "filesystem.delete")

    assert floor == RiskLevel.HIGH_RISK
    assert decide_confidence(intent, floor) == Confidence.RISKY


def test_a_read_only_capability_has_a_read_only_floor(registry):
    assert capability_risk_floor(registry, "filesystem.list") == RiskLevel.READ_ONLY


def test_an_unprovided_capability_has_no_floor(registry):
    assert capability_risk_floor(registry, "telepathy.read") is None


# --- the understanding layer -----------------------------------------


def test_the_prompt_offers_capabilities_and_never_tool_names(registry):
    """A prompt naming tools would put tool selection back in the model."""
    understanding = IntentUnderstanding(FakeClient(), registry)

    prompt = understanding.build_prompt("masaustunu duzenle")

    for capability in registry.capabilities():
        assert capability in prompt
    named = [
        d.name
        for d in registry.list_tools()
        if f"- {d.name}\n" in prompt or f" {d.name} " in prompt
    ]
    assert named == []


@pytest.mark.asyncio
async def test_a_well_formed_reply_becomes_an_understood_intent(registry):
    client = FakeClient(json.dumps(_payload()))

    result = await IntentUnderstanding(client, registry).understand("masaustunu duzenle")

    assert result.understood
    assert result.confidence == Confidence.HIGH
    assert result.intent.required_capabilities == ("filesystem.list", "filesystem.move")


@pytest.mark.asyncio
async def test_an_unparseable_reply_is_reported_not_guessed(registry):
    client = FakeClient("Tabii, hemen yapiyorum!")

    result = await IntentUnderstanding(client, registry).understand("bir sey yap")

    assert not result.understood
    assert result.intent is None
    assert result.error
    assert result.unavailable is False


@pytest.mark.asyncio
async def test_a_retry_recovers_valid_json_after_prose(registry):
    client = FakeClient(contents=["Tabii!", json.dumps(_payload())])

    result = await IntentUnderstanding(client, registry).understand("masaustunu duzenle")

    assert result.understood
    assert len(client.prompts) == 2
    assert result.intent.required_capabilities == ("filesystem.list", "filesystem.move")


@pytest.mark.asyncio
async def test_a_server_failure_degrades_rather_than_raises(registry):
    """Offline must fall back to the deterministic chain, not crash."""
    client = FakeClient(raises=RuntimeError("connection refused"))

    result = await IntentUnderstanding(client, registry).understand("masaustunu duzenle")

    assert not result.understood
    assert result.unavailable is True


@pytest.mark.asyncio
async def test_a_timeout_degrades_rather_than_raises(registry):
    class SlowClient:
        async def chat(self, request):
            await asyncio.sleep(5)

    result = await IntentUnderstanding(SlowClient(), registry, timeout=0.01).understand("x")

    assert result.unavailable is True
    assert "zaman" in result.error


@pytest.mark.asyncio
async def test_an_invented_capability_survives_parsing_but_fails_validation(registry):
    client = FakeClient(json.dumps(_payload(required_capabilities=["mind.read"])))

    result = await IntentUnderstanding(client, registry).understand("aklimi oku")

    assert result.intent is not None
    assert not result.understood
    assert result.validation.unknown_capabilities == ("mind.read",)


# --- context ----------------------------------------------------------


def test_session_state_reaches_the_prompt(registry, tmp_path):
    from hermes.context.conversational_context import ConversationalContext

    context = ConversationalContext()
    context.last_created_file = str(tmp_path / "rapor.txt")
    understanding = IntentUnderstanding(FakeClient(), registry)

    prompt = understanding.build_prompt("onu ac", context=context)

    assert str(tmp_path / "rapor.txt") in prompt


def test_recent_turns_reach_the_prompt(registry):
    understanding = IntentUnderstanding(FakeClient(), registry)

    prompt = understanding.build_prompt(
        "onu ozetle", history=["Chrome'u ac", "Su siteye gir"]
    )

    assert "Su siteye gir" in prompt


def test_an_empty_context_adds_nothing_to_the_prompt():
    assert build_context_block(None, None) == ""


def test_previous_intent_and_task_result_reach_the_prompt(registry):
    from hermes.context.conversational_context import ConversationalContext

    context = ConversationalContext()
    context.last_intent = {
        "goal": "Word dosyasi olustur",
        "required_capabilities": ["document.create"],
    }
    context.last_task_result = "rapor.docx olusturuldu"
    context.last_url = "https://ornek.site/a"
    context.last_browser_url = "https://ornek.site/b"

    prompt = IntentUnderstanding(FakeClient(), registry).build_prompt(
        "basligini degistir", context=context
    )

    assert "Word dosyasi olustur" in prompt
    assert "rapor.docx olusturuldu" in prompt
    assert "https://ornek.site/a" in prompt
    assert "https://ornek.site/b" in prompt


def test_conversation_mode_does_not_require_capabilities(registry):
    intent = AgentIntent.from_dict(
        {"goal": "selam", "mode": "conversation", "reply": "Merhaba", "confidence": 0.9}
    )
    result = validate_intent(intent, set(known_capabilities()), set(registry.capabilities()))
    assert result.ok
