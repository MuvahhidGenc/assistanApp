"""Phase 7.1 — search_files mission verification and PDF E2E."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.mission.engine import MissionEngine
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import build_file_operations_plan
from hermes.mission.step_context import record_search_files_context, scan_files_on_filesystem
from hermes.mission.store import MissionStore
from hermes.security.policy_engine import PolicyDecision
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.base import VerificationStatus
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.registry import create_default_verifier_registry
from hermes.tools.verifiers.specific import SearchFilesVerifier


@pytest.fixture
def mission_root(tmp_path, monkeypatch):
    root = tmp_path / "missions"
    index_path = root / "index.json"
    monkeypatch.setattr("hermes.mission.store.missions_dir", lambda: root)
    monkeypatch.setattr("hermes.mission.store.missions_index_path", lambda: index_path)
    monkeypatch.setattr(
        "hermes.mission.store.ensure_user_dirs",
        lambda: root.mkdir(parents=True, exist_ok=True),
    )
    return root


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.mark.asyncio
async def test_search_files_verifier_pdf_results(tmp_path, monkeypatch):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    pdf = folder / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    (folder / "notes.txt").write_text("not pdf", encoding="utf-8")

    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )

    reg = create_default_registry()
    tool = reg.get("search_files")
    assert tool is not None
    result = await tool.execute(path=str(folder), pattern="*.pdf")
    assert result.success

    verifier = SearchFilesVerifier()
    ctx = VerifierContext(
        tool_name="search_files",
        tool_arguments={"path": str(folder), "pattern": "*.pdf"},
        execution_success=True,
        execution_output=result.output,
        step=MissionStep(
            step_id="search",
            title="Ara",
            verification={"required": True, "method": "search_files_filesystem"},
        ),
    )
    verified = await verifier.verify(ctx)
    assert verified.status == VerificationStatus.VERIFIED
    assert verified.details.get("result_count") == 1
    obs = verified.observation.data if verified.observation else {}
    output = obs.get("verified_output") or {}
    assert all(str(item.get("name", "")).endswith(".pdf") for item in output.get("matches", []))


@pytest.mark.asyncio
async def test_search_files_verifier_empty_is_verified(tmp_path, monkeypatch):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )

    scanned = scan_files_on_filesystem(str(folder), "*.pdf")
    assert scanned["count"] == 0

    verifier = SearchFilesVerifier()
    ctx = VerifierContext(
        tool_name="search_files",
        tool_arguments={"path": str(folder), "pattern": "*.pdf"},
        execution_success=True,
        execution_output=scanned,
    )
    verified = await verifier.verify(ctx)
    assert verified.status == VerificationStatus.VERIFIED
    assert verified.details.get("result_count") == 0


@pytest.mark.asyncio
async def test_search_files_verifier_execution_failed():
    verifier = SearchFilesVerifier()
    ctx = VerifierContext(
        tool_name="search_files",
        tool_arguments={"path": "C:/missing", "pattern": "*.pdf"},
        execution_success=False,
        execution_error="Klasor yok",
    )
    verified = await verifier.verify(ctx)
    assert verified.status == VerificationStatus.FAILED


def test_search_result_persistence_in_mission_state():
    mission = Mission.create("PDF ara")
    step = MissionStep(step_id="search_source_files", title="Ara", tool_name="search_files")
    output = {
        "folder": "C:/Users/x/Downloads",
        "pattern": "*.pdf",
        "count": 2,
        "matches": [
            {"path": "C:/Users/x/Downloads/a.pdf", "name": "a.pdf"},
            {"path": "C:/Users/x/Downloads/b.pdf", "name": "b.pdf"},
        ],
    }
    record_search_files_context(mission, step, output)
    stored = mission.working_context["search_results"]["search_source_files"]
    assert stored["result_count"] == 2
    assert len(stored["matched_files"]) == 2
    assert stored["file_pattern"] == "*.pdf"


@pytest.mark.asyncio
async def test_registry_routes_search_files_to_filesystem_verifier():
    reg = create_default_verifier_registry()
    ctx = VerifierContext(
        tool_name="search_files",
        tool_arguments={"path": "C:/x", "pattern": "*.pdf"},
        execution_success=True,
        execution_output={"folder": "C:/x", "pattern": "*.pdf", "count": 0, "matches": []},
    )
    result = await reg.verify(ctx)
    assert result.method == "search_files_filesystem"


@pytest.mark.asyncio
async def test_pdf_mission_full_e2e(tmp_path, monkeypatch, mission_root, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "test1.pdf").write_bytes(b"%PDF-1.4 one")
    (downloads / "test2.pdf").write_bytes(b"%PDF-1.4 two")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    reports = desktop / "Raporlar"

    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    store = MissionStore()
    mission = store.create_mission(
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
    steps = build_file_operations_plan(mission.user_goal, registry)
    assert len(steps) >= 3
    mission.steps = steps
    mission.plan_validated = True
    mission.status = MissionStatus.RUNNING
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW

    async def execute_local(local_call, run_id):
        tool = registry.get(local_call.name)
        assert tool is not None
        result = await tool.execute(**dict(local_call.arguments))
        return ToolResultPayload(
            tool_call_id=run_id,
            success=bool(result.success),
            output=result.output,
            error=result.error,
        )

    engine = MissionEngine(
        store,
        registry,
        executor,
        execute_local_tool=execute_local,
    )
    result = await engine._execute_plan(mission)  # noqa: SLF001

    assert result.success is True
    assert reports.exists()
    copied = list(reports.glob("*.pdf"))
    assert len(copied) == 2
    search_ctx = (store.load(mission.mission_id).working_context.get("search_results") or {})
    assert any(item.get("result_count") == 2 for item in search_ctx.values())


@pytest.mark.asyncio
async def test_pdf_mission_empty_search_completes_naturally(tmp_path, monkeypatch, mission_root, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    desktop = tmp_path / "Desktop"
    desktop.mkdir()

    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    store = MissionStore()
    mission = store.create_mission("Indirilenler klasorundeki PDF dosyalarini bul.")
    steps = build_file_operations_plan(mission.user_goal, registry)[:1]
    mission.steps = steps
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

    assert result.success is True
    assert any("pdf" in msg.casefold() and "bulamad" in msg.casefold() for msg in result.user_messages)


@pytest.mark.asyncio
async def test_copy_skips_non_pdf_matches(tmp_path, monkeypatch, mission_root, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "good.pdf").write_bytes(b"%PDF")
    (downloads / "bad.txt").write_text("x", encoding="utf-8")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()

    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    store = MissionStore()
    mission = store.create_mission("PDF kopyala")
    steps = build_file_operations_plan("Indirilenlerdeki PDF dosyalarini kopyala", registry)
    mission.steps = steps
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

    reports = desktop / "Raporlar"
    if reports.exists():
        assert not (reports / "bad.txt").exists()
        assert (reports / "good.pdf").exists() or result.success
