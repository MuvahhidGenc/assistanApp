"""Real Windows E2E: user's Downloads PDFs -> Desktop/Raporlar (no mocks)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hermes.build_info import HERMES_BUILD_VERSION  # noqa: E402
from hermes.mission.engine import MissionEngine  # noqa: E402
from hermes.mission.models import MissionStatus  # noqa: E402
from hermes.mission.planner import MissionPlanner  # noqa: E402
from hermes.mission.store import MissionStore  # noqa: E402
from hermes.security.policy_engine import PolicyDecision  # noqa: E402
from hermes.server.models import ToolResultPayload  # noqa: E402
from hermes.tools.registry import create_default_registry  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402


GOAL = (
    "Indirilenler klasorundeki PDF dosyalarini bul ve "
    "masaustunde Raporlar klasoru olusturup oraya kopyala."
)


async def main() -> int:
    downloads = Path.home() / "Downloads"
    reports = Path.home() / "Desktop" / "Raporlar"
    print(f"HERMES_BUILD_VERSION={HERMES_BUILD_VERSION}")
    print(f"downloads={downloads}")
    print(f"reports={reports}")

    registry = create_default_registry()
    store = MissionStore()
    mission = store.create_mission(GOAL)
    planner = MissionPlanner(AsyncMock(), registry)
    plan = await planner.create_plan(mission)
    print(f"plan_source={plan.source} steps={[s.step_id for s in plan.steps]}")

    mission.steps = plan.steps
    mission.plan_validated = True
    mission.status = MissionStatus.RUNNING
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW

    async def execute_local(local_call, run_id):
        tool = registry.get(local_call.name)
        result = await tool.execute(**dict(local_call.arguments))
        return ToolResultPayload(
            tool_call_id=run_id,
            success=bool(result.success),
            output=result.output,
            error=result.error,
        )

    engine = MissionEngine(store, registry, executor, execute_local_tool=execute_local)
    result = await engine._execute_plan(mission)  # noqa: SLF001
    loaded = store.load(mission.mission_id)
    search_ctx = (loaded.working_context.get("search_results") or {}).get("search_source_files", {})
    copy_ctx = (loaded.working_context.get("copy_results") or {}).get("copy_matched_files", {})
    pdf_count = len(list(reports.glob("*.pdf"))) if reports.exists() else 0
    nested = (reports / "Downloads").exists()
    sys_files = list(reports.rglob("*.sys")) if reports.exists() else []

    print(f"success={result.success}")
    print(f"search_result_count={search_ctx.get('result_count')}")
    print(f"copy_verified={copy_ctx.get('verified_count')}")
    print(f"reports_pdf_count={pdf_count}")
    print(f"nested_downloads={nested}")
    print(f"sys_in_reports={len(sys_files)}")
    print(f"summary={result.summary[:500]}")
    return 0 if result.success and not nested and not sys_files else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
