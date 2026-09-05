"""ScreenState → ScreenEntity → resolve → click(x,y) → observe/verify."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes.context.entity_decision import Confidence
from hermes.intent.models import AgentIntent
from hermes.intent.router import IntentRouter, RouteKind
from hermes.screen.bind import entity_click_point
from hermes.screen.loop import needs_visible_search, search_visible_area
from hermes.screen.models import BoundingBox, ScreenEntity, ScreenState
from hermes.screen.observe import build_screen_state
from hermes.screen.plan import build_screen_perception_intent
from hermes.screen.reference import is_literal_click_query, is_screen_perception_task
from hermes.screen.resolve import resolve_screen_reference
from hermes.screen.store import clear_screen_state, remember_screen_state
from hermes.tools.capabilities import select_tool_for_capability
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.base import VerificationStatus
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.specific import (
    ClickScreenVerifier,
    ResolveScreenEntityVerifier,
    ScrollScreenVerifier,
)
from hermes.tools.windows.screen_tools import ResolveScreenEntityTool


def _entity(entity_id: str, text: str, x: int, y: int, w: int = 80, h: int = 20) -> ScreenEntity:
    return ScreenEntity(
        id=entity_id,
        type="text_line",
        role="unknown",
        text=text,
        bbox=BoundingBox(x, y, w, h),
        confidence=0.9,
        source="ocr",
    )


def _state(*entities: ScreenEntity, width: int = 400, height: int = 400) -> ScreenState:
    return ScreenState(
        screenshot={"width": width, "height": height, "path": "mem.png"},
        window={"title": "Example", "width": width, "height": height},
        entities=list(entities),
        text=" ".join(item.text for item in entities),
    )


def _three_videos() -> ScreenState:
    return _state(
        _entity("se_0", "Gunun ozeti", 40, 40),
        _entity("se_1", "Teknoloji ve Yapay Zeka", 160, 180),
        _entity("se_2", "Muzik listesi", 280, 320),
    )


def test_ocr_payload_keeps_spatial_entities():
    payload = {
        "text": "Teknoloji ve Yapay Zeka",
        "screenshot": {"width": 320, "height": 200, "path": "x.png"},
        "window_title": "YouTube",
        "boxes": [
            {"text": "Teknoloji", "x": 160, "y": 160, "w": 80, "h": 16, "conf": 90, "line": 1, "block": 1},
            {"text": "ve", "x": 250, "y": 160, "w": 20, "h": 16, "conf": 80, "line": 1, "block": 1},
            {"text": "Yapay", "x": 280, "y": 160, "w": 50, "h": 16, "conf": 88, "line": 1, "block": 1},
            {"text": "Zeka", "x": 340, "y": 160, "w": 40, "h": 16, "conf": 86, "line": 1, "block": 1},
        ],
    }
    state = build_screen_state(payload, remember=False)
    lines = [item for item in state.entities if item.type == "text_line"]
    assert lines
    assert "Teknoloji" in lines[0].text
    assert lines[0].bbox.w > 0
    assert lines[0].bbox.h > 0


def test_first_entity_wins():
    decision = resolve_screen_reference(_three_videos(), "ekranda gordugun ilk videoyu ac")
    assert decision.auto_selected
    assert decision.chosen == ("se_0",)


def test_first_video_skips_top_chip_and_shorts_row():
    state = _state(
        _entity("chrome_0", "»", 8, 4, w=16, h=12),
        _entity("chrome_1", "» TR", 40, 4, w=36, h=12),
        _entity("bar", "— » YouTube Ara", 8, 6, w=360, h=14),
        _entity("chip_0", "Yemek pisirme", 20, 48, w=70, h=14),
        _entity("chip_1", "Son yuklenenler", 120, 48, w=80, h=14),
        _entity("chip_2", "Canli", 220, 48, w=50, h=14),
        _entity("short_0", "Miizik Canli Futbol", 24, 70, w=72, h=16),
        _entity("short_1", "NOH SURESI TEFSIRI", 140, 70, w=72, h=16),
        _entity("se_0", "Gunun ozeti", 40, 180),
        _entity("se_1", "Teknoloji ve Yapay Zeka", 160, 180),
        _entity("se_2", "Muzik listesi", 280, 180),
        width=400,
        height=400,
    )
    first = resolve_screen_reference(state, "ekranda gordugun ilk videoyu ac")
    assert first.chosen == ("se_0",)
    assert first.chosen[0] not in {"short_0", "chip_0", "chip_1", "bar"}


def test_fresh_bir_video_does_not_reuse_session_entity():
    state = _state(
        _entity("se_6", "Miizik Canli Futbol", 40, 180),
        _entity("se_8", "NOH SURESI TEFSIRI", 160, 180),
        _entity("se_9", "Gunun ozeti", 280, 180),
    )
    decision = resolve_screen_reference(
        state,
        "ekranda girdigin bir videoyu ac",
        session_entity_id="se_8",
    )
    assert decision.reason != "session_reference"
    assert not (decision.auto_selected and decision.chosen == ("se_8",))


def test_screen_rescan_second_video_ignores_session():
    state = _state(
        _entity("se_0", "Gunun ozeti", 40, 180),
        _entity("se_1", "Teknoloji ve Yapay Zeka", 160, 180),
        _entity("se_2", "Muzik listesi", 280, 180),
    )
    decision = resolve_screen_reference(
        state,
        "ekranda gordugun ikinci videoyu ac",
        session_entity_id="se_0",
    )
    assert decision.chosen == ("se_1",)
    assert decision.reason != "session_reference"


def test_spatial_rank_uses_target_class_not_toolbar():
    state = _state(
        _entity("chrome_0", "»", 8, 4, w=16, h=12),
        _entity("chrome_1", "» TR", 40, 4, w=36, h=12),
        _entity("bar", "— » YouTube Ara", 8, 6, w=360, h=14),
        _entity("se_0", "Gunun ozeti", 40, 80),
        _entity("se_1", "Teknoloji ve Yapay Zeka", 160, 180),
        _entity("se_2", "Muzik listesi", 280, 300),
        width=400,
        height=400,
    )
    first = resolve_screen_reference(state, "ekranda gordugun ilk videoyu ac")
    second = resolve_screen_reference(state, "gordugun ikinci videoyu ac")
    middle = resolve_screen_reference(state, "ekranda gordugun ortadaki videoyu ac")
    assert first.chosen == ("se_0",)
    assert second.chosen == ("se_1",)
    assert middle.chosen == ("se_1",)
    assert first.chosen[0] not in {"chrome_0", "chrome_1", "bar"}


def test_middle_entity_wins():
    decision = resolve_screen_reference(_three_videos(), "ortadaki videoyu ac")
    assert decision.auto_selected
    assert decision.chosen == ("se_1",)


def test_left_and_right_spatial_reference():
    state = _state(
        _entity("left", "Bir", 20, 100),
        _entity("right", "Iki", 300, 100),
    )
    assert resolve_screen_reference(state, "soldaki videoyu ac").chosen == ("left",)
    assert resolve_screen_reference(state, "sagdaki videoyu ac").chosen == ("right",)


def test_text_semantic_candidate():
    decision = resolve_screen_reference(_three_videos(), "teknoloji videosunu ac")
    assert decision.auto_selected
    assert decision.chosen == ("se_1",)


def test_ambiguous_candidate_asks():
    state = _state(
        _entity("a", "Video A", 40, 80),
        _entity("b", "Video B", 40, 200),
    )
    decision = resolve_screen_reference(state, "su videoyu ac")
    assert decision.needs_user_input
    assert not decision.auto_selected
    assert decision.clarification


def test_no_candidate_is_not_success():
    decision = resolve_screen_reference(ScreenState(), "ortadaki videoyu ac")
    assert not decision.auto_selected
    assert decision.confidence is Confidence.LOW


def test_entity_bbox_becomes_click_coordinates():
    entity = _entity("se_1", "Teknoloji", 100, 40, w=80, h=20)
    assert entity_click_point(entity) == (140, 50)


@pytest.mark.asyncio
async def test_resolve_tool_binds_coordinates():
    clear_screen_state()
    remember_screen_state(_three_videos())
    result = await ResolveScreenEntityTool().execute(reference="ortadaki videoyu ac")
    assert result.success
    assert result.output["x"] == 200
    assert result.output["y"] == 190
    assert result.output["entity_id"] == "se_1"
    clear_screen_state()


@pytest.mark.asyncio
async def test_failed_resolution_is_not_success():
    clear_screen_state()
    remember_screen_state(ScreenState())
    result = await ResolveScreenEntityTool().execute(reference="ortadaki videoyu ac")
    assert result.success is False
    assert result.output["found"] is False
    clear_screen_state()


@pytest.mark.asyncio
async def test_ambiguous_resolution_asks_instead_of_clicking():
    clear_screen_state()
    remember_screen_state(
        _state(_entity("a", "Video A", 10, 10), _entity("b", "Video B", 10, 200))
    )
    result = await ResolveScreenEntityTool().execute(reference="su videoyu ac")
    assert result.success is False
    assert result.output["needs_user"] is True
    assert result.output.get("needs_scroll") is not True
    clear_screen_state()


@pytest.mark.asyncio
async def test_no_candidate_triggers_scroll_reobserve_loop():
    attempts = {"resolve": 0, "scroll": 0, "observe": 0}

    async def resolve():
        attempts["resolve"] += 1
        return SimpleNamespace(success=True, output={"found": True, "x": 10, "y": 20})

    async def scroll():
        attempts["scroll"] += 1
        return SimpleNamespace(success=True, output={"direction": "down"})

    async def observe():
        attempts["observe"] += 1
        return SimpleNamespace(success=True, output={"text": "ok"})

    result = await search_visible_area(
        resolve=resolve,
        scroll=scroll,
        observe=observe,
        guard_allows=lambda name, args: True,
        initial=SimpleNamespace(success=False, output={"found": False, "needs_scroll": True}),
    )
    assert result.success
    assert attempts["scroll"] == 1
    assert attempts["observe"] == 1
    assert attempts["resolve"] == 1


@pytest.mark.asyncio
async def test_scroll_loop_stops_when_guard_refuses():
    async def resolve():
        return SimpleNamespace(success=False, output={"found": False, "needs_scroll": True})

    result = await search_visible_area(
        resolve=resolve,
        scroll=lambda: None,
        observe=lambda: None,
        guard_allows=lambda name, args: False,
        initial=SimpleNamespace(success=False, output={"found": False, "needs_scroll": True}),
    )
    assert result.success is False
    assert needs_visible_search(result)


def test_literal_click_text_still_allowed_for_named_text():
    from hermes.agent.local_intent import match_local_intent

    intent = match_local_intent("Abone ol yazisina tikla")
    assert intent is not None
    assert intent.request.name == "click_text"
    assert "Abone" in intent.request.arguments["text"]


def test_quoted_text_is_literal_click():
    assert is_literal_click_query('Ekranda "Abone ol" yazisina tikla', "Abone ol")
    assert not is_literal_click_query("teknoloji videosunu ac", "teknoloji")
    assert not is_literal_click_query("teknolojiyle ilgili videoyu ac", "teknolojiyle ilgili")


def test_catalog_find_video_is_search_not_homepage_waiting():
    from hermes.agent.local_intent import guess_local_action, match_local_intent

    phrase = "Youtube acip teknoloji ile ilgili bir video bul ve ac"
    assert not is_screen_perception_task(phrase)
    intent = match_local_intent(phrase) or guess_local_action(phrase)
    assert intent is not None
    assert intent.request.name == "open_url"
    assert "search_query=" in str(intent.request.arguments.get("url") or "")
    assert "teknoloji" in str(intent.request.arguments.get("url") or "").casefold()
    assert build_screen_perception_intent(phrase) is None


def test_content_plus_video_is_screen_perception():
    from hermes.agent.local_intent import guess_local_action, match_local_intent

    for phrase in (
        "teknoloji videosunu ac",
        "teknolojiyle ilgili videoyu ac",
        "Tevhid videosunu ac",
        "ekranda gordugun ilk videoyu ac",
    ):
        assert is_screen_perception_task(phrase), phrase
        assert match_local_intent(phrase) is None or match_local_intent(phrase).request.name != "click_text"
        guessed = guess_local_action(phrase)
        assert guessed is None, phrase


def test_session_entity_scores_deictic_reference():
    state = _state(
        _entity("a", "Video A", 40, 80),
        _entity("b", "Video B", 40, 200),
    )
    asked = resolve_screen_reference(state, "bu videoyu ac")
    assert asked.needs_user_input
    remembered = resolve_screen_reference(state, "bu videoyu ac", session_entity_id="b")
    assert remembered.auto_selected
    assert remembered.chosen == ("b",)


@pytest.mark.asyncio
async def test_resolve_uses_bound_state_not_stale_singleton():
    stale = _state(_entity("old", "Eski", 10, 10))
    fresh = _three_videos()
    remember_screen_state(stale)
    result = await ResolveScreenEntityTool().execute(
        reference="ortadaki videoyu ac",
        screen_state=fresh.to_dict(),
    )
    assert result.success
    assert result.output["entity_id"] == "se_1"
    clear_screen_state()


@pytest.mark.asyncio
async def test_click_verifies_only_when_target_appears_in_title_or_url():
    async def observe(name, arguments, run_id):
        return SimpleNamespace(
            success=True,
            output={
                "text": "Teknoloji ve Yapay Zeka oynatiliyor",
                "window_title": "Teknoloji ve Yapay Zeka - YouTube",
                "url": "https://www.youtube.com/watch?v=abc",
                "screenshot": {"width": 100, "height": 80},
                "boxes": [
                    {"text": "Teknoloji", "x": 16, "y": 16, "w": 20, "h": 10, "conf": 90, "line": 1, "block": 1}
                ],
            },
        )

    ctx = VerifierContext(
        tool_name="click",
        tool_arguments={"x": 200, "y": 190, "text": "Teknoloji ve Yapay Zeka", "entity_id": "se_1"},
        execution_success=True,
        execution_output={"x": 200, "y": 190, "text": "Teknoloji ve Yapay Zeka", "entity_id": "se_1"},
        observe_tool=observe,
        run_id="t4",
    )
    result = await ClickScreenVerifier().verify(ctx)
    assert result.status is VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_click_unknown_keeps_target_unproven_not_generic_reason():
    from hermes.screen.models import ScreenState
    from hermes.screen.store import remember_screen_state
    from hermes.tools.verifiers.registry import create_default_verifier_registry

    remember_screen_state(
        ScreenState(
            state_id="pre_home",
            screenshot={"width": 100, "height": 80},
            window={"title": "YouTube", "url": "https://www.youtube.com"},
            text="home",
        )
    )

    async def observe(name, arguments, run_id):
        return SimpleNamespace(
            success=True,
            output={
                "text": "home",
                "window_title": "YouTube",
                "url": "https://www.youtube.com",
                "screenshot": {"width": 100, "height": 80},
                "boxes": [
                    {"text": "home", "x": 16, "y": 16, "w": 20, "h": 10, "conf": 90, "line": 1, "block": 1}
                ],
            },
        )

    ctx = VerifierContext(
        tool_name="click",
        tool_arguments={
            "x": 80,
            "y": 190,
            "text": "Miizik Canli Futbol",
            "entity_id": "se_6",
            "bbox": {"x": 40, "y": 170, "w": 80, "h": 20},
            "state_id": "pre_home",
        },
        execution_success=True,
        execution_output={"x": 80, "y": 190, "text": "Miizik Canli Futbol", "entity_id": "se_6"},
        observe_tool=observe,
        run_id="t5",
    )
    result = await create_default_verifier_registry().verify(ctx)
    assert result.status is VerificationStatus.UNKNOWN
    assert result.details.get("reason") == "target_unproven"
    assert result.details.get("reason") != "structured_output_unverified"
    assert result.details.get("click_x") == 80
    assert result.details.get("pre_state_id") == "pre_home"
    clear_screen_state()


@pytest.mark.asyncio
async def test_click_wrong_destination_is_failed():
    from hermes.screen.models import ScreenState
    from hermes.screen.store import remember_screen_state

    remember_screen_state(
        ScreenState(
            state_id="pre_home",
            screenshot={"width": 100, "height": 80},
            window={"title": "YouTube", "url": "https://www.youtube.com"},
            text="home",
        )
    )

    async def observe(name, arguments, run_id):
        return SimpleNamespace(
            success=True,
            output={
                "text": "Baska video",
                "window_title": "Baska video - YouTube",
                "url": "https://www.youtube.com/watch?v=zzz",
                "screenshot": {"width": 100, "height": 80},
                "boxes": [
                    {"text": "Baska", "x": 16, "y": 16, "w": 20, "h": 10, "conf": 90, "line": 1, "block": 1}
                ],
            },
        )

    ctx = VerifierContext(
        tool_name="click",
        tool_arguments={"x": 80, "y": 190, "text": "Miizik Canli Futbol", "entity_id": "se_6"},
        execution_success=True,
        execution_output={"x": 80, "y": 190, "text": "Miizik Canli Futbol", "entity_id": "se_6"},
        observe_tool=observe,
        run_id="t6",
    )
    result = await ClickScreenVerifier().verify(ctx)
    assert result.status is VerificationStatus.FAILED
    assert result.details.get("reason") == "wrong_target_opened"
    clear_screen_state()


def test_zero_coordinates_are_valid():
    from hermes.mission.engine import _bound_argument_missing

    assert _bound_argument_missing("x", 0) is False
    assert _bound_argument_missing("y", 0) is False
    assert _bound_argument_missing("x", None) is True


def test_natural_language_screen_reference_bypasses_click_text():
    from hermes.agent.local_intent import match_local_intent

    for phrase in (
        "Ekranda gordugun ilk videoyu ac.",
        "Su an ekranda olanlardan en ustteki videoyu ac.",
        "Ortadaki videoyu ac.",
        "Asagidaki videolardan ortadakini ac.",
    ):
        intent = match_local_intent(phrase)
        assert intent is None or intent.request.name != "click_text", phrase
        assert is_screen_perception_task(phrase)
        assert not is_literal_click_query(phrase)


def test_multi_step_screen_task_is_capability_plan_not_open_url():
    from hermes.agent.local_intent import match_local_intent

    message = (
        "Youtube'a gir, ekranda teknolojiyle ilgili bir video bul, "
        "ac ve bana videonun basligini soyle."
    )
    assert match_local_intent(message) is None
    intent = build_screen_perception_intent(message)
    assert intent is not None
    assert [step.capability for step in intent.plan] == [
        "browser.navigate",
        "screen.observe",
        "screen.resolve",
        "screen.click",
        "screen.observe",
        "screen.read",
    ]
    assert intent.plan[0].inputs["url"] == "https://www.youtube.com"

    router = IntentRouter(create_default_registry())
    plan = router.route(intent, confidence=Confidence.HIGH)
    assert plan.kind is RouteKind.CAPABILITY_PLAN
    tools = [step.tool_name for step in plan.steps]
    assert tools[0] == "open_url"
    assert "read_screen_text" in tools
    assert "resolve_screen_entity" in tools
    assert "click" in tools
    assert "click_text" not in tools
    click = next(step for step in plan.steps if step.tool_name == "click")
    assert click.argument_bindings
    assert {item["argument"] for item in click.argument_bindings} >= {"x", "y"}
    resolve = next(step for step in plan.steps if step.tool_name == "resolve_screen_entity")
    assert any(item.get("argument") == "screen_state" for item in resolve.argument_bindings)


def test_screen_observe_prefers_ocr_tool():
    registry = create_default_registry()
    selected = select_tool_for_capability(registry, "screen.observe", {})
    assert selected is not None
    assert selected.tool_name == "read_screen_text"


def test_existing_screen_read_route_unchanged():
    router = IntentRouter(create_default_registry())
    intent = AgentIntent.from_dict(
        {
            "goal": "ekranda ne yaziyor",
            "plan": [{"capability": "screen.read", "inputs": {}}],
            "confidence": 0.9,
        }
    )
    plan = router.route(intent, confidence=Confidence.HIGH)
    assert plan.kind is RouteKind.CAPABILITY_PLAN
    assert plan.steps[0].tool_name == "read_screen_text"


@pytest.mark.asyncio
async def test_failed_resolve_verification_is_not_success():
    ctx = VerifierContext(
        tool_name="resolve_screen_entity",
        tool_arguments={"reference": "orta"},
        execution_success=False,
        execution_output={"found": False, "needs_scroll": True},
        execution_error="ekranda hedef bulunamadi",
    )
    result = await ResolveScreenEntityVerifier().verify(ctx)
    assert result.status is VerificationStatus.FAILED


@pytest.mark.asyncio
async def test_click_without_observe_does_not_verify():
    ctx = VerifierContext(
        tool_name="click",
        tool_arguments={"x": 10, "y": 20},
        execution_success=True,
        execution_output={"x": 10, "y": 20},
        observe_tool=None,
    )
    result = await ClickScreenVerifier().verify(ctx)
    assert result.status is not VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_click_observe_failure_is_not_success():
    async def observe(name, arguments, run_id):
        return SimpleNamespace(success=False, output=None, error="ocr failed")

    ctx = VerifierContext(
        tool_name="click",
        tool_arguments={"x": 10, "y": 20},
        execution_success=True,
        execution_output={"x": 10, "y": 20},
        observe_tool=observe,
        run_id="t1",
    )
    result = await ClickScreenVerifier().verify(ctx)
    assert result.status is VerificationStatus.FAILED


@pytest.mark.asyncio
async def test_click_and_scroll_observe_can_verify():
    async def observe(name, arguments, run_id):
        assert name == "read_screen_text"
        return SimpleNamespace(
            success=True,
            output={
                "text": "ok",
                "screenshot": {"width": 100, "height": 80},
                "boxes": [
                    {"text": "ok", "x": 16, "y": 16, "w": 20, "h": 10, "conf": 90, "line": 1, "block": 1}
                ],
            },
        )

    click_ctx = VerifierContext(
        tool_name="click",
        tool_arguments={"x": 10, "y": 20},
        execution_success=True,
        execution_output={"x": 10, "y": 20},
        observe_tool=observe,
        run_id="t2",
    )
    scroll_ctx = VerifierContext(
        tool_name="scroll",
        tool_arguments={"direction": "down"},
        execution_success=True,
        execution_output={"direction": "down"},
        observe_tool=observe,
        run_id="t3",
    )
    assert (await ClickScreenVerifier().verify(click_ctx)).status is VerificationStatus.UNKNOWN
    assert (await ScrollScreenVerifier().verify(scroll_ctx)).status is VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_resolve_tool_does_not_bypass_missing_state():
    clear_screen_state()
    result = await ResolveScreenEntityTool().execute(reference="ilk video")
    assert result.success is False
    assert result.output["needs_scroll"] is True
