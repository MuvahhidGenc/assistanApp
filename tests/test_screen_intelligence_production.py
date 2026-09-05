"""Production-path Screen Intelligence: process_message must not bypass the plan."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.orchestrator import AgentOrchestrator, AgentPhase
from hermes.config.settings import AppSettings
from hermes.mission.models import MissionStatus, MissionStepStatus
from hermes.mission.store import MissionStore
from hermes.screen.observe import attach_screen_state
from hermes.screen.store import clear_screen_state
from hermes.server.models import Session
from hermes.tools.base import ToolExecutionResult
from hermes.tools.verifiers.base import VerificationStatus


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings.load()


def _video_ocr(*, title: str = "YouTube", url: str = "") -> dict:
    return attach_screen_state(
        {
            "text": "Gunun ozeti Teknolojiyle ilgili Yapay Zeka Muzik listesi",
            "screenshot": {"width": 640, "height": 400, "path": "mem.png"},
            "window_title": title,
            "url": url,
            "window": {"title": title, "url": url, "width": 640, "height": 400},
            "boxes": [
                {
                    "text": "»",
                    "x": 16,
                    "y": 8,
                    "w": 16,
                    "h": 12,
                    "conf": 80,
                    "line": 0,
                    "block": 0,
                },
                {
                    "text": "TR",
                    "x": 40,
                    "y": 8,
                    "w": 20,
                    "h": 12,
                    "conf": 80,
                    "line": 0,
                    "block": 0,
                },
                {
                    "text": "— » YouTube Ara",
                    "x": 16,
                    "y": 10,
                    "w": 400,
                    "h": 14,
                    "conf": 70,
                    "line": 0,
                    "block": 9,
                },
                {
                    "text": "Gunun",
                    "x": 64,
                    "y": 48,
                    "w": 50,
                    "h": 16,
                    "conf": 90,
                    "line": 1,
                    "block": 1,
                },
                {
                    "text": "ozeti",
                    "x": 120,
                    "y": 48,
                    "w": 50,
                    "h": 16,
                    "conf": 90,
                    "line": 1,
                    "block": 1,
                },
                {
                    "text": "Teknolojiyle",
                    "x": 240,
                    "y": 180,
                    "w": 80,
                    "h": 16,
                    "conf": 90,
                    "line": 2,
                    "block": 2,
                },
                {
                    "text": "ve",
                    "x": 325,
                    "y": 180,
                    "w": 20,
                    "h": 16,
                    "conf": 80,
                    "line": 2,
                    "block": 2,
                },
                {
                    "text": "Yapay",
                    "x": 350,
                    "y": 180,
                    "w": 50,
                    "h": 16,
                    "conf": 88,
                    "line": 2,
                    "block": 2,
                },
                {
                    "text": "Zeka",
                    "x": 405,
                    "y": 180,
                    "w": 40,
                    "h": 16,
                    "conf": 86,
                    "line": 2,
                    "block": 2,
                },
                {
                    "text": "Muzik",
                    "x": 480,
                    "y": 320,
                    "w": 50,
                    "h": 16,
                    "conf": 90,
                    "line": 3,
                    "block": 3,
                },
                {
                    "text": "listesi",
                    "x": 535,
                    "y": 320,
                    "w": 50,
                    "h": 16,
                    "conf": 90,
                    "line": 3,
                    "block": 3,
                },
            ],
        }
    )


def _ambiguous_ocr() -> dict:
    return attach_screen_state(
        {
            "text": "Video A Video B",
            "screenshot": {"width": 400, "height": 400, "path": "mem.png"},
            "window_title": "YouTube",
            "boxes": [
                {
                    "text": "Video",
                    "x": 40,
                    "y": 80,
                    "w": 50,
                    "h": 16,
                    "conf": 90,
                    "line": 1,
                    "block": 1,
                },
                {
                    "text": "A",
                    "x": 95,
                    "y": 80,
                    "w": 16,
                    "h": 16,
                    "conf": 90,
                    "line": 1,
                    "block": 1,
                },
                {
                    "text": "Video",
                    "x": 40,
                    "y": 220,
                    "w": 50,
                    "h": 16,
                    "conf": 90,
                    "line": 2,
                    "block": 2,
                },
                {
                    "text": "B",
                    "x": 95,
                    "y": 220,
                    "w": 16,
                    "h": 16,
                    "conf": 90,
                    "line": 2,
                    "block": 2,
                },
            ],
        }
    )


def _empty_ocr() -> dict:
    return attach_screen_state(
        {
            "text": "Sayfa hala yukleniyor, icerik listesi henuz gorunmuyor.",
            "screenshot": {},
            "boxes": [],
        }
    )


def _orchestrator(settings: AppSettings, tmp_path):
    server = MagicMock()
    server.create_session = AsyncMock(return_value=Session(id="sess-screen"))
    server.create_run = AsyncMock()
    server.update_session = AsyncMock(return_value=Session(id="sess-screen"))
    server.chat = AsyncMock(side_effect=RuntimeError("understanding offline"))
    store = MissionStore(tmp_path / "missions")
    return AgentOrchestrator(settings, server, mission_store=store), server


def _install_fakes(orch: AgentOrchestrator, ocr_factory, *, flags: dict | None = None):
    calls: list[str] = []
    resolve_states: list[str] = []
    clicks: list[dict] = []
    flags = flags if flags is not None else {"scrolled": False}

    read = orch._registry.get("read_screen_text")
    click = orch._registry.get("click")
    scroll = orch._registry.get("scroll")
    resolve = orch._registry.get("resolve_screen_entity")
    open_url = orch._registry.get("open_url")
    real_resolve = resolve.execute

    async def fake_read(**kwargs):
        calls.append("read_screen_text")
        return ToolExecutionResult(success=True, output=ocr_factory())

    async def fake_click(x=0, y=0, **kwargs):
        payload = {"x": x, "y": y}
        payload.update({key: kwargs[key] for key in ("entity_id", "text") if key in kwargs})
        clicks.append(payload)
        calls.append("click")
        return ToolExecutionResult(success=True, output=payload)

    async def fake_scroll(**kwargs):
        flags["scrolled"] = True
        calls.append("scroll")
        return ToolExecutionResult(success=True, output={"direction": kwargs.get("direction", "down")})

    async def wrapped_resolve(**kwargs):
        calls.append("resolve_screen_entity")
        state = kwargs.get("screen_state")
        if isinstance(state, dict):
            resolve_states.append(str(state.get("state_id") or ""))
        return await real_resolve(**kwargs)

    async def fake_open_url(**kwargs):
        calls.append("open_url")
        return ToolExecutionResult(success=True, output={"url": kwargs.get("url") or ""})

    read.execute = fake_read
    click.execute = fake_click
    scroll.execute = fake_scroll
    resolve.execute = wrapped_resolve
    if open_url is not None:
        open_url.execute = fake_open_url
    return calls, resolve_states, clicks, flags


def _planned_tools(orch: AgentOrchestrator) -> list[str]:
    trace = orch.state.metadata.get("faz_f_trace") or {}
    return list(trace.get("SELECTED_LOCAL_TOOLS") or [])


def _active_mission(orch: AgentOrchestrator):
    mission_id = str((orch.state.metadata or {}).get("mission_id") or "")
    if mission_id:
        loaded = orch._mission_store.load(mission_id)
        if loaded is not None:
            return loaded
    return orch._mission_store.load_active()


@pytest.mark.asyncio
async def test_process_message_first_video_is_not_read_only_completed(settings, tmp_path):
    clear_screen_state()
    orch, _server = _orchestrator(settings, tmp_path)
    calls, _states, clicks, _flags = _install_fakes(orch, _video_ocr)

    result = await orch.process_message("ekranda gordugun ilk videoyu ac")

    tools = _planned_tools(orch)
    assert "resolve_screen_entity" in tools
    assert "click" in tools
    assert "read_screen_text" in tools
    assert "click_text" not in tools
    assert "resolve_screen_entity" in calls
    assert "click" in calls
    assert calls != ["read_screen_text"]
    assert clicks
    clicked_text = str(clicks[0].get("text") or "")
    assert "»" not in clicked_text
    assert "Ara" not in clicked_text
    mission = _active_mission(orch)
    assert mission is not None
    read_only = [step.tool_name for step in mission.steps if step.tool_name]
    assert read_only != ["read_screen_text"]
    click_step = next(step for step in mission.steps if step.tool_name == "click")
    assert click_step.status != MissionStepStatus.COMPLETED
    assert click_step.verification_status != VerificationStatus.VERIFIED.value
    assert mission.status != MissionStatus.COMPLETED
    assert orch.state.phase != AgentPhase.COMPLETED
    assert "local tool" not in result.lower()
    clear_screen_state()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    (
        "ortadaki videoyu ac",
        "sagdaki videoyu ac",
        "teknolojiyle ilgili videoyu ac",
    ),
)
async def test_process_message_natural_screen_targets_use_resolve_click(settings, tmp_path, message):
    clear_screen_state()
    orch, _server = _orchestrator(settings, tmp_path)
    calls, _states, clicks, _flags = _install_fakes(orch, _video_ocr)

    await orch.process_message(message)

    tools = _planned_tools(orch)
    assert "resolve_screen_entity" in tools
    assert "click" in tools
    assert "click_text" not in tools
    assert "resolve_screen_entity" in calls
    assert "click" in calls
    assert clicks
    clear_screen_state()


@pytest.mark.asyncio
async def test_process_message_resolve_miss_scrolls_and_reobserves_state_b(settings, tmp_path):
    clear_screen_state()
    orch, _server = _orchestrator(settings, tmp_path)
    empty = _empty_ocr()
    visible = _video_ocr()
    flags = {"scrolled": False}

    def factory():
        return visible if flags["scrolled"] else empty

    calls, resolve_states, clicks, _flags = _install_fakes(orch, factory, flags=flags)

    await orch.process_message("ortadaki videoyu ac")

    assert "scroll" in calls
    assert calls.count("read_screen_text") >= 2
    assert calls.count("resolve_screen_entity") >= 2
    assert resolve_states
    assert resolve_states[0] == empty["state_id"]
    assert resolve_states[-1] == visible["state_id"]
    assert clicks
    clear_screen_state()


@pytest.mark.asyncio
async def test_process_message_ambiguous_waits_and_resume_rebinds(settings, tmp_path):
    clear_screen_state()
    orch, _server = _orchestrator(settings, tmp_path)
    calls, _states, _clicks, _flags = _install_fakes(orch, _ambiguous_ocr)

    first = await orch.process_message("su videoyu ac")
    mission = _active_mission(orch)
    assert mission is not None
    assert mission.status == MissionStatus.WAITING_FOR_USER
    assert orch.state.phase in (AgentPhase.WAITING_FOR_USER, AgentPhase.AWAITING_APPROVAL)
    assert mission.working_context.get("pending_screen_resolve")
    assert "resolve_screen_entity" in calls
    assert "click" not in calls
    assert first

    pending = mission.working_context.get("pending_screen_resolve") or {}
    presented = list(pending.get("presented_order") or pending.get("candidates") or [])
    assert len(presented) >= 2
    expected_second = presented[1]

    second = await orch.process_message("ikincisini")
    resumed = orch._mission_store.load(mission.mission_id)
    assert resumed is not None
    resolve = next(step for step in resumed.steps if step.tool_name == "resolve_screen_entity")
    assert "ikincisini" in str(resolve.tool_arguments.get("reference") or "")
    assert resolve.tool_arguments.get("session_entity_id") == expected_second
    assert calls.count("resolve_screen_entity") >= 2
    assert second
    clear_screen_state()


@pytest.mark.asyncio
async def test_process_message_waiting_new_task_does_not_reuse_pending(settings, tmp_path):
    clear_screen_state()
    orch, _server = _orchestrator(settings, tmp_path)
    _install_fakes(orch, _ambiguous_ocr)

    await orch.process_message("su videoyu ac")
    waiting = _active_mission(orch)
    assert waiting is not None
    assert waiting.status == MissionStatus.WAITING_FOR_USER
    waiting_id = waiting.mission_id

    await orch.process_message("Youtube acip teknoloji ile ilgili bir video bul ve ac")
    leftover = orch._mission_store.load(waiting_id)
    assert leftover is None or leftover.status != MissionStatus.WAITING_FOR_USER
    tools = _planned_tools(orch)
    assert "open_url" in tools or tools[0:1] != []
    active = orch._mission_store.load_active()
    assert active is None or active.mission_id != waiting_id or active.status != MissionStatus.WAITING_FOR_USER
    clear_screen_state()


@pytest.mark.asyncio
async def test_process_message_waiting_cancel_stops_pending(settings, tmp_path):
    clear_screen_state()
    orch, _server = _orchestrator(settings, tmp_path)
    _install_fakes(orch, _ambiguous_ocr)

    await orch.process_message("su videoyu ac")
    waiting = _active_mission(orch)
    assert waiting is not None
    result = await orch.process_message("vazgec")
    loaded = orch._mission_store.load(waiting.mission_id)
    assert loaded is None or loaded.status == MissionStatus.CANCELLED
    assert "iptal" in result.casefold()
    clear_screen_state()


@pytest.mark.asyncio
async def test_process_message_click_unknown_does_not_complete(settings, tmp_path):
    clear_screen_state()
    orch, _server = _orchestrator(settings, tmp_path)
    calls, _states, clicks, _flags = _install_fakes(orch, _video_ocr)

    await orch.process_message("ortadaki videoyu ac")

    mission = _active_mission(orch)
    assert mission is not None
    click_step = next(step for step in mission.steps if step.tool_name == "click")
    assert click_step.verification_status == VerificationStatus.UNKNOWN.value
    assert click_step.status != MissionStepStatus.COMPLETED
    assert mission.status != MissionStatus.COMPLETED
    assert orch.state.phase != AgentPhase.COMPLETED
    assert "click" in calls
    assert clicks
    clear_screen_state()
