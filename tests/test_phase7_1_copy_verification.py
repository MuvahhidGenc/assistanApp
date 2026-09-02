"""Phase 7.1 — copy_search_matches filesystem verification."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes.mission.engine import MissionEngine
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import build_file_operations_plan
from hermes.mission.step_context import (
    expected_copy_destination,
    record_search_files_context,
    run_copy_search_matches,
    select_copy_matches,
    verify_copy_destinations,
)
from hermes.mission.store import MissionStore
from hermes.security.policy_engine import PolicyDecision
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.base import VerificationStatus
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.registry import create_default_verifier_registry
from hermes.tools.verifiers.specific import CopyFileVerifier


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


def _patch_home(monkeypatch, base: Path) -> None:
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: base)


@pytest.mark.asyncio
async def test_copy_file_verifier_filesystem_pass(tmp_path, monkeypatch):
    src = tmp_path / "a.pdf"
    dst_dir = tmp_path / "Raporlar"
    dst_dir.mkdir()
    dst = dst_dir / "a.pdf"
    src.write_bytes(b"%PDF content")
    dst.write_bytes(b"%PDF content")

    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )

    verifier = CopyFileVerifier()
    ctx = VerifierContext(
        tool_name="copy_file",
        tool_arguments={"source": str(src), "destination": str(dst_dir)},
        execution_success=True,
        execution_output={"source": str(src), "destination": str(dst), "exists": True, "verified": True},
    )
    result = await verifier.verify(ctx)
    assert result.status == VerificationStatus.VERIFIED
    assert result.method == "copy_file_filesystem"


@pytest.mark.asyncio
async def test_copy_file_verifier_success_claim_but_missing_file(tmp_path, monkeypatch):
    src = tmp_path / "a.pdf"
    dst_dir = tmp_path / "Raporlar"
    src.write_bytes(b"%PDF")
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )

    verifier = CopyFileVerifier()
    ctx = VerifierContext(
        tool_name="copy_file",
        tool_arguments={"source": str(src), "destination": str(dst_dir)},
        execution_success=True,
        execution_output={"exists": True, "verified": True},
    )
    result = await verifier.verify(ctx)
    assert result.status == VerificationStatus.FAILED
    assert result.details.get("reason") == "destination_missing"


@pytest.mark.asyncio
async def test_copy_file_verifier_routed_via_registry(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    dst_dir = tmp_path / "out"
    dst_dir.mkdir()
    src.write_bytes(b"x")
    (dst_dir / "doc.pdf").write_bytes(b"x")
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )

    reg = create_default_verifier_registry()
    ctx = VerifierContext(
        tool_name="copy_file",
        tool_arguments={"source": str(src), "destination": str(dst_dir)},
        execution_success=True,
        execution_output={"exists": True, "verified": True},
    )
    result = await reg.verify(ctx)
    assert result.status == VerificationStatus.VERIFIED
    assert result.method == "copy_file_filesystem"


@pytest.mark.asyncio
async def test_verify_copy_destinations_detects_missing(tmp_path, monkeypatch):
    src = tmp_path / "one.pdf"
    src.write_bytes(b"pdf")
    dest = tmp_path / "Raporlar"
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )
    matches = [{"path": str(src), "name": "one.pdf", "size": 3}]
    report = verify_copy_destinations(matches, str(dest), pattern="*.pdf")
    assert report["ok"] is False
    assert report["missing"]


@pytest.mark.asyncio
async def test_run_copy_search_matches_idempotent(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    reports = tmp_path / "Desktop" / "Raporlar"
    downloads.mkdir(parents=True)
    reports.mkdir(parents=True)
    src = downloads / "keep.pdf"
    src.write_bytes(b"same-content")
    (reports / "keep.pdf").write_bytes(b"same-content")
    _patch_home(monkeypatch, tmp_path)

    matches = [{"path": str(src), "name": "keep.pdf", "size": 12}]
    report = await run_copy_search_matches(matches, str(reports))
    assert report["ok"] is True
    assert report["copied"] == 0
    assert report["skipped"] == 1
    assert report["verified_count"] == 1


@pytest.mark.asyncio
async def test_copy_mission_e2e_three_pdfs(tmp_path, monkeypatch, mission_root, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    for i in range(3):
        (downloads / f"doc{i}.pdf").write_bytes(f"%PDF-{i}".encode())
    (downloads / "notes.txt").write_text("skip", encoding="utf-8")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    reports = desktop / "Raporlar"
    _patch_home(monkeypatch, tmp_path)

    store = MissionStore()
    mission = store.create_mission(
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
    mission.steps = build_file_operations_plan(mission.user_goal, registry)
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
    copied = list(reports.glob("*.pdf"))
    assert len(copied) == 3
    assert not (reports / "notes.txt").exists()
    copy_step = next(s for s in store.load(mission.mission_id).steps if s.step_id == "copy_matched_files")
    assert copy_step.status == MissionStepStatus.COMPLETED
    assert copy_step.verification_method == "copy_search_matches_filesystem"


@pytest.mark.asyncio
async def test_copy_mission_idempotent_second_run(tmp_path, monkeypatch, mission_root, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "a.pdf").write_bytes(b"%PDF-a")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    reports = desktop / "Raporlar"
    _patch_home(monkeypatch, tmp_path)

    goal = (
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
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

    engine = MissionEngine(
        MissionStore(),
        registry,
        executor,
        execute_local_tool=execute_local,
    )

    for _ in range(2):
        store = MissionStore()
        mission = store.create_mission(goal)
        mission.steps = build_file_operations_plan(goal, registry)
        mission.plan_validated = True
        mission.status = MissionStatus.RUNNING
        store.save(mission)
        result = await engine._execute_plan(mission)  # noqa: SLF001
        assert result.success is True

    assert len(list(reports.glob("*.pdf"))) == 1


@pytest.mark.asyncio
async def test_copy_mission_fails_when_destination_missing_after_copy(tmp_path, monkeypatch, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "lost.pdf").write_bytes(b"%PDF")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    reports = desktop / "Raporlar"
    _patch_home(monkeypatch, tmp_path)

    store = MissionStore()
    mission = store.create_mission("PDF kopyala")
    steps = build_file_operations_plan(
        "Indirilenlerdeki PDF dosyalarini masaustunde Raporlar klasorune kopyala",
        registry,
    )
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

    original = run_copy_search_matches

    async def broken_copy(matches, destination, **kwargs):
        report = await original(matches, destination, **kwargs)
        for path in list(report.get("verified_files") or []):
            Path(path).unlink(missing_ok=True)
        report["ok"] = False
        report["missing"] = [str(expected_copy_destination(m["path"], destination)) for m in matches]
        report["verified_count"] = 0
        return report

    monkeypatch.setattr("hermes.mission.engine.run_copy_search_matches", broken_copy)

    result = await engine._execute_plan(mission)  # noqa: SLF001
    assert result.success is False
    copy_step = next(s for s in store.load(mission.mission_id).steps if s.step_id == "copy_matched_files")
    assert copy_step.status == MissionStepStatus.FAILED


@pytest.mark.asyncio
async def test_find_dependency_output_passes_search_to_copy(tmp_path):
    mission = Mission.create("PDF")
    search_step = MissionStep(
        step_id="search_source_files",
        title="Ara",
        action=StepAction.TOOL,
        tool_name="search_files",
    )
    copy_step = MissionStep(
        step_id="copy_matched_files",
        title="Kopyala",
        action=StepAction.LOGICAL,
        depends_on=["search_source_files"],
        metadata={"logical_kind": "copy_search_matches", "destination": "/dest"},
    )
    mission.steps = [search_step, copy_step]
    output = {
        "folder": str(tmp_path / "Downloads"),
        "pattern": "*.pdf",
        "count": 2,
        "matches": [
            {"path": "/a.pdf", "name": "a.pdf"},
            {"path": "/b.pdf", "name": "b.pdf"},
        ],
    }
    record_search_files_context(mission, search_step, output)

    from hermes.mission.step_context import find_dependency_output

    info = find_dependency_output(mission, copy_step, tool_name="search_files")
    assert info is not None
    assert info["count"] == 2
    assert len(info["matches"]) == 2
    ctx = mission.working_context["search_results"]["search_source_files"]
    assert ctx["result_count"] == 2
    assert ctx["file_pattern"] == "*.pdf"


@pytest.mark.asyncio
async def test_bulk_copy_no_twenty_file_cap(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    reports = tmp_path / "Desktop" / "Raporlar"
    downloads.mkdir(parents=True)
    reports.mkdir(parents=True)
    matches = []
    for i in range(45):
        p = downloads / f"f{i:02d}.pdf"
        p.write_bytes(f"pdf-{i}".encode())
        matches.append({"path": str(p), "name": p.name, "size": len(f"pdf-{i}")})
    _patch_home(monkeypatch, tmp_path)

    report = await run_copy_search_matches(matches, str(reports), pattern="*.pdf")
    assert report["ok"] is True
    assert report["verified_count"] == 45
    assert len(list(reports.glob("*.pdf"))) == 45
    assert not any(reports.rglob("*.sys"))


@pytest.mark.asyncio
async def test_copy_only_pdf_from_explicit_match_list(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    reports = tmp_path / "Desktop" / "Raporlar"
    downloads.mkdir(parents=True)
    reports.mkdir(parents=True)
    (downloads / "a.pdf").write_bytes(b"a")
    (downloads / "b.pdf").write_bytes(b"b")
    (downloads / "driver.sys").write_bytes(b"sys")
    _patch_home(monkeypatch, tmp_path)

    matches = [
        {"path": str(downloads / "a.pdf"), "name": "a.pdf"},
        {"path": str(downloads / "b.pdf"), "name": "b.pdf"},
    ]
    report = await run_copy_search_matches(matches, str(reports), pattern="*.pdf")
    assert report["ok"] is True
    assert sorted(p.name for p in reports.glob("*.pdf")) == ["a.pdf", "b.pdf"]
    assert not (reports / "driver.sys").exists()


@pytest.mark.asyncio
async def test_copy_skips_sys_injected_into_match_list(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    reports = tmp_path / "Desktop" / "Raporlar"
    downloads.mkdir(parents=True)
    reports.mkdir(parents=True)
    (downloads / "a.pdf").write_bytes(b"a")
    sys_file = downloads / "driver.sys"
    sys_file.write_bytes(b"sys")
    _patch_home(monkeypatch, tmp_path)

    matches = [
        {"path": str(downloads / "a.pdf"), "name": "a.pdf"},
        {"path": str(sys_file), "name": "driver.sys"},
    ]
    report = await run_copy_search_matches(matches, str(reports), pattern="*.pdf")
    assert report["ok"] is True
    assert (reports / "a.pdf").exists()
    assert not (reports / "driver.sys").exists()
    assert str(sys_file) in list(report.get("skipped_non_pdf") or [])


@pytest.mark.asyncio
async def test_copy_mixed_downloads_subfolders_only_pdfs(tmp_path, monkeypatch, mission_root, registry):
    downloads = tmp_path / "Downloads"
    sub = downloads / "AMD Sanallastirma Acma" / "nested"
    sub.mkdir(parents=True)
    for name in ("one.pdf", "two.pdf", "three.pdf"):
        (sub / name).write_bytes(b"%PDF")
    (sub / "a.sys").write_bytes(b"sys")
    (sub / "b.sys").write_bytes(b"sys")
    (downloads / "x.txt").write_text("x", encoding="utf-8")
    (sub / "y.txt").write_text("y", encoding="utf-8")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    reports = desktop / "Raporlar"
    _patch_home(monkeypatch, tmp_path)

    store = MissionStore()
    goal = (
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
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

    assert result.success is True
    copied = list(reports.glob("*.pdf"))
    assert len(copied) == 3
    assert not (reports / "Downloads").exists()
    assert not any(reports.rglob("*.sys"))
    assert not any(reports.rglob("*.txt"))


@pytest.mark.asyncio
async def test_flat_destination_not_nested_downloads_path(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads" / "sub"
    reports = tmp_path / "Desktop" / "Raporlar"
    downloads.mkdir(parents=True)
    reports.mkdir(parents=True)
    src = downloads / "rapor.pdf"
    src.write_bytes(b"%PDF")
    _patch_home(monkeypatch, tmp_path)

    dest = expected_copy_destination(str(src), str(reports))
    assert dest == reports / "rapor.pdf"
    report = await run_copy_search_matches(
        [{"path": str(src), "name": "rapor.pdf"}],
        str(reports),
        pattern="*.pdf",
    )
    assert report["ok"] is True
    assert (reports / "rapor.pdf").exists()
    assert not (reports / "Downloads").exists()


@pytest.mark.asyncio
async def test_partial_copy_failure_continues_other_pdfs(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    reports = tmp_path / "Desktop" / "Raporlar"
    downloads.mkdir(parents=True)
    reports.mkdir(parents=True)
    good = downloads / "good.pdf"
    bad = downloads / "bad.pdf"
    bad.mkdir()
    good.write_bytes(b"ok")
    _patch_home(monkeypatch, tmp_path)

    import shutil

    original_copy2 = shutil.copy2

    def flaky_copy2(src, dst, *args, **kwargs):
        if Path(src).name == "bad.pdf":
            raise PermissionError("denied")
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", flaky_copy2)

    matches = [
        {"path": str(good), "name": "good.pdf"},
        {"path": str(bad), "name": "bad.pdf"},
    ]
    report = await run_copy_search_matches(
        matches,
        str(reports),
        pattern="*.pdf",
    )
    assert report["ok"] is False
    assert report["partial"] is True
    assert (reports / "good.pdf").exists()
    assert report["failed"] == 1
    assert "bad.pdf" in str(report.get("failed_files"))


def test_planner_pdf_mission_paths(registry):
    goal = (
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
    steps = build_file_operations_plan(goal, registry)
    search = next(s for s in steps if s.step_id == "search_source_files")
    copy_step = next(s for s in steps if s.step_id == "copy_matched_files")
    assert search.tool_arguments.get("pattern") == "*.pdf"
    assert "Raporlar" in str(copy_step.metadata.get("destination") or "")
    assert copy_step.metadata.get("logical_kind") == "copy_search_matches"
    assert "search_source_files" in copy_step.depends_on


def test_select_copy_matches_filters_sys():
    matches = [
        {"path": r"C:\Downloads\a.pdf", "name": "a.pdf"},
        {"path": r"C:\Downloads\driver.sys", "name": "driver.sys"},
    ]
    accepted, skipped = select_copy_matches(matches, "*.pdf")
    assert len(accepted) == 1
    assert accepted[0]["name"] == "a.pdf"
    assert skipped == [r"C:\Downloads\driver.sys"]


def test_normalize_replaces_ai_copy_file_plan(registry):
    from hermes.mission.models import MissionStep, StepAction
    from hermes.mission.planner import normalize_file_operations_plan

    goal = (
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
    ai_steps = [
        MissionStep(
            step_id="search_source_files",
            title="Ara",
            action=StepAction.TOOL,
            tool_name="search_files",
            tool_arguments={"path": "Downloads", "pattern": "*.pdf"},
        ),
        MissionStep(
            step_id="copy_one",
            title="Kopyala",
            action=StepAction.TOOL,
            tool_name="copy_file",
            tool_arguments={
                "source": r"C:\Downloads\AMD\amigendrv64.sys",
                "destination": r"Raporlar\Downloads\AMD\amigendrv64.sys",
            },
            depends_on=["search_source_files"],
        ),
    ]
    normalized = normalize_file_operations_plan(goal, ai_steps, registry)
    assert any(
        step.metadata.get("logical_kind") == "copy_search_matches"
        for step in normalized
        if step.action == StepAction.LOGICAL
    )
    assert not any(step.tool_name == "copy_file" for step in normalized)


@pytest.mark.asyncio
async def test_planner_prefers_file_operations_plan_over_ai(registry):
    from unittest.mock import AsyncMock, MagicMock

    from hermes.mission.models import Mission
    from hermes.mission.planner import MissionPlanner

    goal = (
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
    mission = Mission.create(goal)
    client = AsyncMock()
    client.chat = AsyncMock(side_effect=AssertionError("AI planner must not run"))
    planner = MissionPlanner(client, registry)
    result = await planner.create_plan(mission)
    assert result.success is True
    assert result.source == "file_operations_heuristic"
    assert any(step.step_id == "copy_matched_files" for step in result.steps)
    copy_step = next(s for s in result.steps if s.step_id == "copy_matched_files")
    assert copy_step.metadata.get("logical_kind") == "copy_search_matches"
    assert copy_step.metadata.get("destination")


def test_validate_plan_preserves_copy_metadata(registry):
    from hermes.mission.planner import build_file_operations_plan
    from hermes.mission.validator import validate_plan_steps

    goal = (
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
    raw = [step.to_dict() for step in build_file_operations_plan(goal, registry)]
    validated = validate_plan_steps(raw, registry)
    assert validated.ok
    copy_step = next(s for s in validated.steps if s.step_id == "copy_matched_files")
    assert copy_step.metadata.get("logical_kind") == "copy_search_matches"
    assert "Raporlar" in str(copy_step.metadata.get("destination") or "")


def test_copy_executor_destination_is_flat_file_path(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads" / "nested"
    reports = tmp_path / "Desktop" / "Raporlar"
    downloads.mkdir(parents=True)
    src = downloads / "doc.pdf"
    src.write_bytes(b"x")
    _patch_home(monkeypatch, tmp_path)
    dest = expected_copy_destination(str(src), str(reports))
    assert dest.name == "doc.pdf"
    assert dest.parent == reports
