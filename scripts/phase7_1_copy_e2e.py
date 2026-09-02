"""Real filesystem E2E for PDF copy_search_matches (Windows-safe temp sandbox)."""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hermes.mission.engine import MissionEngine  # noqa: E402
from hermes.mission.models import MissionStatus  # noqa: E402
from hermes.mission.planner import build_file_operations_plan  # noqa: E402
from hermes.mission.store import MissionStore  # noqa: E402
from hermes.security.policy_engine import PolicyDecision  # noqa: E402
from hermes.server.models import ToolResultPayload  # noqa: E402
from hermes.tools.registry import create_default_registry  # noqa: E402


async def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="hermes_pdf_e2e_"))
    downloads = sandbox / "Downloads" / "AMD Sanallastirma Acma"
    reports = sandbox / "Desktop" / "Raporlar"
    downloads.mkdir(parents=True)
    reports.mkdir(parents=True)

    for i in range(45):
        (downloads / f"doc{i:02d}.pdf").write_bytes(f"%PDF-{i}".encode())
    (downloads / "amigendrv64.sys").write_bytes(b"sys")
    (downloads / "notes.txt").write_text("skip", encoding="utf-8")

    import hermes.tools.windows.file_tools as ft

    real_home = Path.home
    real_resolve = ft.resolve_user_path

    def fake_home() -> Path:
        return sandbox

    def fake_resolve(raw: str) -> Path:
        text = (raw or "").strip()
        if not text:
            raise ValueError("path gerekli")
        path = Path(text).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (sandbox / "Desktop" / path).resolve()

    ft.resolve_user_path = fake_resolve  # type: ignore[method-assign]
    Path.home = fake_home  # type: ignore[method-assign]

    registry = create_default_registry()
    goal = (
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
    store = MissionStore()
    mission = store.create_mission(goal)
    mission.steps = build_file_operations_plan(goal, registry)
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

    copied = list(reports.glob("*.pdf"))
    nested_downloads = reports / "Downloads"
    ok = (
        result.success
        and len(copied) == 45
        and not nested_downloads.exists()
        and not (reports / "amigendrv64.sys").exists()
    )
    print(f"sandbox={sandbox}")
    print(f"success={result.success} pdf_count={len(copied)} nested_downloads={nested_downloads.exists()}")
    print(f"summary={result.summary[:300]}")
    shutil.rmtree(sandbox, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
