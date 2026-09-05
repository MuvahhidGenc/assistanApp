"""Production-path: screen result binding, click verify, media vs folder routing."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes.agent.goal_parser import parse_goal
from hermes.agent.local_intent import match_local_intent
from hermes.agent.tool_intent import match_tool_intent
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.entity_decision import Confidence
from hermes.mission.models import MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.reality_verification import verify_pdf_document
from hermes.mission.store import MissionStore
from hermes.screen.models import BoundingBox, ScreenEntity, ScreenState
from hermes.screen.reference import looks_like_media_open
from hermes.screen.resolve import (
    bind_presented_screen_choice,
    build_screen_result_set,
    resolve_screen_reference,
)
from hermes.screen.store import clear_screen_state, remember_screen_state
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.base import VerificationStatus
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.specific import ClickScreenVerifier, OpenPathVerifier
from hermes.tools.windows.file_tools import WriteFileTool
from unittest.mock import MagicMock


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


def _video_state() -> ScreenState:
    entities = [
        ScreenEntity(
            id="se_1",
            type="video",
            role="video",
            text="Teknolojik Tekillik: Altinci ve Son Caga Az Kaldi",
            bbox=BoundingBox(100, 100, 400, 80),
            confidence=0.9,
        ),
        ScreenEntity(
            id="se_2",
            type="video",
            role="video",
            text="Cicek ile Teknoloji",
            bbox=BoundingBox(100, 220, 400, 80),
            confidence=0.9,
        ),
        ScreenEntity(
            id="se_3",
            type="video",
            role="video",
            text="Yapay Zeka ve Gelecek",
            bbox=BoundingBox(100, 340, 400, 80),
            confidence=0.9,
        ),
    ]
    return ScreenState(
        state_id="state-present",
        entities=entities,
        text="\n".join(item.text for item in entities),
        window={"title": "teknoloji - YouTube - Google Chrome", "url": ""},
    )


def test_presented_order_binds_one_and_center():
    state = _video_state()
    result_set = build_screen_result_set(
        state=state,
        presented_order=["se_1", "se_2", "se_3"],
        clarification="3 farkli oge buldum",
    )
    assert bind_presented_screen_choice("1'i ac", result_set) == "se_1"
    assert bind_presented_screen_choice("ilkini ac", result_set) == "se_1"
    assert bind_presented_screen_choice("ortadakini ac", result_set) == "se_2"
    assert bind_presented_screen_choice("2. videoyu ac", result_set) == "se_2"
    assert bind_presented_screen_choice("buldugun ilk videoyu ac", result_set) == "se_1"

    decision = resolve_screen_reference(state, "1'i ac", result_set=result_set)
    assert decision.chosen == ("se_1",)
    assert decision.reason == "presented_order"
    assert decision.confidence is Confidence.HIGH


def test_presented_order_does_not_rescore_against_reading_order():
    entities = [
        ScreenEntity(
            id="se_bottom",
            type="video",
            text="Ucuncu Sunulan",
            bbox=BoundingBox(10, 10, 50, 20),
            confidence=0.9,
        ),
        ScreenEntity(
            id="se_top",
            type="video",
            text="Ilk Sunulan",
            bbox=BoundingBox(10, 400, 50, 20),
            confidence=0.9,
        ),
    ]
    state = ScreenState(state_id="s", entities=entities, text="x")
    result_set = build_screen_result_set(
        state=state,
        presented_order=["se_top", "se_bottom"],
    )
    decision = resolve_screen_reference(state, "1'i ac", result_set=result_set)
    assert decision.chosen == ("se_top",)
    assert decision.reason == "presented_order"


def test_muzik_video_is_media_not_music_folder():
    message = "muzik tekillik videosunu ac"
    assert looks_like_media_open(message)
    parsed = parse_goal(message)
    assert "Music" not in (parsed.source_location or "")
    intent = match_local_intent(message)
    if intent is not None:
        assert intent.request.name != "open_path"
        path = str(intent.request.arguments.get("path") or "")
        assert "Music" not in path
    resolver_result = match_tool_intent(message, ConversationalContext(), registry=create_default_registry())
    if resolver_result.intent is not None:
        assert resolver_result.intent.request.name != "open_path"
        path = str(resolver_result.intent.request.arguments.get("path") or "")
        assert "Music" not in path


def test_video_open_without_filesystem_task_skips_folder_local_intent():
    message = "teknolojik tekillik videosunu ac"
    intent = match_local_intent(message)
    if intent is not None:
        assert intent.request.name != "open_path"


@pytest.mark.asyncio
async def test_click_verified_when_screen_state_changes():
    clear_screen_state()
    pre = _video_state()
    remember_screen_state(pre)

    async def observe(name, arguments, run_id):
        post = {
            "text": "watch page content changed",
            "window_title": "Teknolojik Tekillik - YouTube - Google Chrome",
            "url": "https://youtube.com/watch?v=1",
            "screenshot": {"width": 800, "height": 600},
            "boxes": [
                {
                    "text": "Teknolojik",
                    "x": 20,
                    "y": 20,
                    "w": 40,
                    "h": 12,
                    "conf": 90,
                    "line": 1,
                    "block": 1,
                }
            ],
        }
        return SimpleNamespace(success=True, output=post)

    result = await ClickScreenVerifier().verify(
        VerifierContext(
            tool_name="click",
            tool_arguments={
                "entity_id": "se_1",
                "text": "Teknolojik Tekillik: Altinci ve Son Caga Az Kaldi",
                "state_id": pre.state_id,
                "x": 300,
                "y": 140,
            },
            execution_success=True,
            execution_output={"entity_id": "se_1", "state_id": pre.state_id},
            observe_tool=observe,
            run_id="click-ok",
        )
    )
    clear_screen_state()
    assert result.status is VerificationStatus.VERIFIED
    assert result.details.get("reason") in {
        "target_in_title_or_url",
        "target_tokens_in_title_or_url",
        "target_left_view_after_click",
        "destination_and_content_changed",
    }


@pytest.mark.asyncio
async def test_click_unknown_is_not_verified_without_evidence():
    clear_screen_state()
    pre = _video_state()
    remember_screen_state(pre)

    async def observe(name, arguments, run_id):
        return SimpleNamespace(
            success=True,
            output={
                "text": pre.text,
                "window_title": pre.window["title"],
                "url": "",
                "screenshot": {"width": 800, "height": 600},
                "boxes": [
                    {
                        "text": "Teknolojik Tekillik: Altinci ve Son Caga Az Kaldi",
                        "x": 100,
                        "y": 100,
                        "w": 400,
                        "h": 80,
                        "conf": 90,
                        "line": 1,
                        "block": 1,
                    }
                ],
            },
        )

    result = await ClickScreenVerifier().verify(
        VerifierContext(
            tool_name="click",
            tool_arguments={
                "entity_id": "se_1",
                "text": "Teknolojik Tekillik: Altinci ve Son Caga Az Kaldi",
                "state_id": pre.state_id,
            },
            execution_success=True,
            execution_output={"entity_id": "se_1"},
            observe_tool=observe,
            run_id="click-unknown",
        )
    )
    clear_screen_state()
    assert result.status is VerificationStatus.UNKNOWN
    assert result.details.get("reason") == "target_unproven"


@pytest.mark.asyncio
async def test_open_path_unverified_is_not_verified_success(tmp_path):
    target = tmp_path / "doc.pdf"
    written = await WriteFileTool().execute(path=str(target), content="Tevhid makalesi")
    assert written.success
    assert verify_pdf_document(target)["ok"]
    result = await OpenPathVerifier().verify(
        VerifierContext(
            tool_name="open_path",
            tool_arguments={"path": str(target)},
            execution_success=True,
            execution_output={
                "path": str(target),
                "opened": True,
                "verified": False,
                "window_title": None,
            },
        )
    )
    assert result.status is VerificationStatus.UNKNOWN
    assert result.details.get("artifact_ok") is True


@pytest.mark.asyncio
async def test_open_path_verified_when_window_observed(tmp_path):
    target = tmp_path / "doc.pdf"
    await WriteFileTool().execute(path=str(target), content="Tevhid makalesi")
    result = await OpenPathVerifier().verify(
        VerifierContext(
            tool_name="open_path",
            tool_arguments={"path": str(target)},
            execution_success=True,
            execution_output={
                "path": str(target),
                "opened": True,
                "verified": True,
                "window_title": "doc.pdf - Reader",
            },
        )
    )
    assert result.status is VerificationStatus.VERIFIED


def test_fake_pdf_artifact_fails():
    path = Path(tempfile.mkdtemp()) / "fake.pdf"
    path.write_text("not a pdf", encoding="utf-8")
    check = verify_pdf_document(path)
    assert check["ok"] is False


@pytest.mark.asyncio
async def test_mission_pending_binds_presented_first_async():
    clear_screen_state()
    state = _video_state()
    remember_screen_state(state)
    result_set = build_screen_result_set(
        state=state,
        presented_order=["se_1", "se_2", "se_3"],
    )
    store = MissionStore()
    mission = store.create_mission("su videoyu ac")
    mission.status = MissionStatus.WAITING_FOR_USER
    mission.working_context["pending_screen_resolve"] = {
        "step_id": "resolve_1",
        "reference": "su videoyu ac",
        "candidates": ["se_1", "se_2", "se_3"],
        "presented_order": ["se_1", "se_2", "se_3"],
        "screen_result_set": result_set,
        "state_id": state.state_id,
    }
    mission.working_context["last_screen_result_set"] = result_set
    mission.steps = [
        MissionStep(
            step_id="resolve_1",
            title="resolve",
            action=StepAction.TOOL,
            tool_name="resolve_screen_entity",
            tool_arguments={"reference": "su videoyu ac"},
            status=MissionStepStatus.FAILED,
        )
    ]
    mission.user_interventions = [{"type": "user_response", "response": "1'i ac"}]
    store.save(mission)

    from hermes.mission.engine import MissionEngine
    from hermes.tools.windows.screen_tools import ResolveScreenEntityTool

    engine = MissionEngine(store, create_default_registry(), MagicMock())
    engine._apply_pending_screen_resolve(mission)
    step = mission.steps[0]
    assert step.tool_arguments.get("session_entity_id") == "se_1"
    tool_result = await ResolveScreenEntityTool().execute(
        reference=str(step.tool_arguments["reference"]),
        session_entity_id=step.tool_arguments.get("session_entity_id"),
        screen_result_set=step.tool_arguments.get("screen_result_set"),
        screen_state=state.to_dict(),
    )
    assert tool_result.success
    assert tool_result.output.get("entity_id") == "se_1"
    assert tool_result.output.get("reason") == "presented_order"
    clear_screen_state()
