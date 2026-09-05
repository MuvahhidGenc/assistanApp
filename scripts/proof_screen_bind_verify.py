"""EXE-parity proof for presented-order bind + click verification."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from hermes.build_info import BUILD_LABEL, HERMES_BUILD_VERSION
from hermes.mission.engine import MissionEngine
from hermes.mission.models import MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.store import MissionStore
from hermes.screen.models import BoundingBox, ScreenEntity, ScreenState
from hermes.screen.resolve import build_screen_result_set
from hermes.screen.store import clear_screen_state, remember_screen_state
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.base import VerificationStatus
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.specific import ClickScreenVerifier
from hermes.tools.windows.screen_tools import ResolveScreenEntityTool
from unittest.mock import MagicMock


def _state() -> ScreenState:
    entities = [
        ScreenEntity(
            id="se_tekillik",
            type="video",
            role="video",
            text="Teknolojik Tekillik: Altinci ve Son Caga Az Kaldi",
            bbox=BoundingBox(120, 180, 520, 90),
            confidence=0.92,
        ),
        ScreenEntity(
            id="se_cicek",
            type="video",
            role="video",
            text="Cicek ile Teknoloji",
            bbox=BoundingBox(120, 300, 520, 90),
            confidence=0.9,
        ),
        ScreenEntity(
            id="se_uza",
            type="video",
            role="video",
            text="Uzay ve Bilim",
            bbox=BoundingBox(120, 420, 520, 90),
            confidence=0.88,
        ),
    ]
    return ScreenState(
        state_id="yt-results",
        entities=entities,
        text="\n".join(e.text for e in entities),
        window={"title": "teknoloji - YouTube - Google Chrome", "url": "https://www.youtube.com/results?search_query=teknoloji"},
    )


async def main() -> None:
    print(f"HERMES_BUILD_VERSION={HERMES_BUILD_VERSION}")
    print(f"BUILD_LABEL={BUILD_LABEL}")
    clear_screen_state()
    state = _state()
    remember_screen_state(state)

    # Direct semantic resolve for the video title (no YouTube-specific code).
    direct = await ResolveScreenEntityTool().execute(
        reference="teknolojik tekillik videosunu ac",
        screen_state=state.to_dict(),
    )
    assert direct.success, direct.error
    assert direct.output["entity_id"] == "se_tekillik", direct.output
    print("resolve_tekillik", direct.output["entity_id"], direct.output.get("reason"), direct.output.get("text")[:40])

    async def observe_ok(name, arguments, run_id):
        return SimpleNamespace(
            success=True,
            output={
                "text": "Izleniyor Teknolojik Tekillik",
                "window_title": "Teknolojik Tekillik: Altinci ve Son Caga Az Kaldi - YouTube - Google Chrome",
                "url": "https://www.youtube.com/watch?v=abc123",
                "screenshot": {"width": 1280, "height": 720},
                "boxes": [{"text": "Teknolojik", "x": 10, "y": 10, "w": 40, "h": 12, "conf": 90, "line": 1, "block": 1}],
            },
        )

    click_verify = await ClickScreenVerifier().verify(
        VerifierContext(
            tool_name="click",
            tool_arguments={
                "entity_id": direct.output["entity_id"],
                "text": direct.output["text"],
                "state_id": state.state_id,
                "x": direct.output["x"],
                "y": direct.output["y"],
            },
            execution_success=True,
            execution_output=dict(direct.output),
            observe_tool=observe_ok,
            run_id="prove-click",
        )
    )
    assert click_verify.status is VerificationStatus.VERIFIED, click_verify.details
    print("click_verify", click_verify.status.value, click_verify.details.get("reason"))

    # Ambiguous list -> user picks 1 -> same presented entity, reason=presented_order
    result_set = build_screen_result_set(
        state=state,
        presented_order=["se_tekillik", "se_cicek", "se_uza"],
    )
    store = MissionStore()
    mission = store.create_mission("su videoyu ac")
    mission.status = MissionStatus.WAITING_FOR_USER
    mission.working_context["pending_screen_resolve"] = {
        "step_id": "resolve_1",
        "reference": "su videoyu ac",
        "presented_order": list(result_set["presented_order"]),
        "candidates": list(result_set["presented_order"]),
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
    engine = MissionEngine(store, create_default_registry(), MagicMock())
    engine._apply_pending_screen_resolve(mission)
    step = mission.steps[0]
    assert step.tool_arguments.get("session_entity_id") == "se_tekillik"
    rebound = await ResolveScreenEntityTool().execute(
        reference=str(step.tool_arguments["reference"]),
        session_entity_id=step.tool_arguments.get("session_entity_id"),
        screen_result_set=step.tool_arguments.get("screen_result_set"),
        screen_state=state.to_dict(),
    )
    assert rebound.success
    assert rebound.output["entity_id"] == "se_tekillik"
    assert rebound.output["reason"] == "presented_order"
    print("one_open", rebound.output["entity_id"], rebound.output["reason"], "no_rescoring=OK")
    clear_screen_state()
    print("PROOF_OK")


if __name__ == "__main__":
    asyncio.run(main())
