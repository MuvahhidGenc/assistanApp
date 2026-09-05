"""Phase 6 context continuation and natural mission summary regression tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.conversation_flow import handle_meta_conversation, is_user_facing_summary
from hermes.agent.local_intent import LocalIntent, LocalToolRequest, summarize_local_result
from hermes.agent.local_verify import verify_rename_path_result
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.server.models import ToolResultPayload
from hermes.tools.pc_manager import parse_pc_command
from hermes.tools.registry import create_default_registry


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    folder = tmp_path / "Desktop"
    folder.mkdir()
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: folder / name,
    )
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: folder,
    )
    return folder


def test_active_file_modify_content(resolver, tmp_path):
    file_path = tmp_path / "notlar.txt"
    file_path.write_text("Phase 6 test", encoding="utf-8")
    ctx = ConversationalContext(active_file=str(file_path.resolve()))
    result = resolver.resolve("İçeriğini Hermes çalışıyor olarak değiştir.", ctx)
    assert not result.ambiguous
    assert result.intent is not None
    assert result.intent.request.name == "write_file"
    assert result.intent.request.arguments["path"] == str(file_path.resolve())
    assert result.intent.request.arguments["content"] == "Hermes çalışıyor"


def test_content_text_does_not_match_filename_stem(resolver, tmp_path):
    file_path = tmp_path / "Hermes.txt"
    file_path.write_text("old", encoding="utf-8")
    ctx = ConversationalContext(
        active_file=str(file_path.resolve()),
        recent_files=[str(file_path.resolve())],
    )
    result = resolver.resolve("İçeriğini Hermes çalışıyor olarak değiştir.", ctx)
    assert not result.ambiguous
    assert result.intent.request.arguments["path"] == str(file_path.resolve())
    assert result.intent.request.arguments["content"] == "Hermes çalışıyor"


def test_active_file_rename(resolver, tmp_path):
    file_path = tmp_path / "notlar.txt"
    file_path.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(active_file=str(file_path.resolve()))
    result = resolver.resolve("Bu dosyanın adını üçüncü.txt yap.", ctx)
    assert not result.ambiguous
    assert result.intent.request.name == "rename_path"
    assert result.intent.request.arguments["new_name"] == "üçüncü.txt"


def test_last_created_file_rename(resolver, tmp_path):
    file_path = tmp_path / "notlar.txt"
    file_path.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(last_created_file=str(file_path.resolve()))
    result = resolver.resolve("Son dosyanın adını üçüncü.txt yap.", ctx)
    assert not result.ambiguous
    assert result.intent.request.name == "rename_path"
    assert Path(result.intent.request.arguments["path"]).name == "notlar.txt"


def test_rename_context_update():
    ctx = ConversationalContext(
        active_file=r"C:\Desktop\HermesPhase6\notlar.txt",
        last_created_file=r"C:\Desktop\HermesPhase6\notlar.txt",
        last_opened_file=r"C:\Desktop\HermesPhase6\notlar.txt",
        recent_files=[r"C:\Desktop\HermesPhase6\notlar.txt"],
    )
    ctx.update_from_tool(
        "rename_path",
        {
            "source": r"C:\Desktop\HermesPhase6\notlar.txt",
            "destination": r"C:\Desktop\HermesPhase6\üçüncü.txt",
            "verified": True,
        },
        success=True,
    )
    assert ctx.active_file.endswith("üçüncü.txt")
    assert ctx.last_created_file.endswith("üçüncü.txt")
    assert ctx.last_opened_file.endswith("üçüncü.txt")
    assert not any(item.endswith("notlar.txt") for item in ctx.recent_files)


def test_natural_mission_summary():
    ctx = ConversationalContext()
    ctx.record_verified_action("HermesPhase6 klasorunu olusturdum")
    ctx.record_verified_action("notlar.txt dosyasini olusturdum ve actim")
    turn = handle_meta_conversation("Ne yaptın?", ctx)
    assert turn.handled
    assert "HermesPhase6" in turn.response
    assert "notlar.txt" in turn.response
    assert "open_app" not in turn.response.casefold()
    assert "write_file" not in turn.response.casefold()


def test_internal_tool_names_filtered_from_summary():
    assert is_user_facing_summary("HermesPhase6 klasorunu olusturdum.") is True
    assert is_user_facing_summary("Son islem: open_app.") is False
    assert is_user_facing_summary('{"tool": "write_file"}') is False


def test_no_context_update_without_verification():
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "write_file",
        {"path": r"C:\Desktop\test.txt", "verification_failed": True},
        success=True,
    )
    assert ctx.active_file is None
    ctx.update_from_tool("write_file", {"path": r"C:\Desktop\test.txt"}, success=True)
    assert ctx.active_file is None


@pytest.mark.asyncio
async def test_verify_rename_path_result(tmp_path):
    src = tmp_path / "notlar.txt"
    dst = tmp_path / "ucuncu.txt"
    src.write_text("x", encoding="utf-8")
    src.rename(dst)
    raw = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"source": str(src), "destination": str(dst)},
    )
    verified = await verify_rename_path_result(
        raw, {"path": str(src), "new_name": "ucuncu.txt"}
    )
    assert verified.success is True
    assert verified.output["verified"] is True


@pytest.mark.asyncio
async def test_orchestrator_context_only_after_verification(settings, tmp_path):
    ctx = ConversationalContext()
    orchestrator = AgentOrchestrator(settings, MagicMock())
    unverified = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"path": str(tmp_path / "x.txt")},
    )
    orchestrator._update_conversational_context(ctx, "write_file", unverified)
    assert ctx.active_file is None

    verified = ToolResultPayload(
        tool_call_id="2",
        success=True,
        output={"path": str(tmp_path / "x.txt"), "verified": True},
    )
    intent = LocalIntent(
        LocalToolRequest("write_file", {"path": str(tmp_path / "x.txt"), "content": "a"}),
        summary="Dosya olusturulacak",
    )
    orchestrator._update_conversational_context(ctx, "write_file", verified, intent)
    assert ctx.active_file is not None


def _make_real_pc_process(desktop: Path):
    registry = create_default_registry()

    async def process(self, command, run_id="", skip_approval=False, *, user_message="", verify=True):
        tool_name, arguments = parse_pc_command(command)
        tool = registry.get(tool_name)
        if not tool:
            return ToolResultPayload(tool_call_id="x", success=False, error="unknown")
        exec_result = await tool.execute(**arguments)
        output = dict(exec_result.output) if isinstance(exec_result.output, dict) else {}
        if exec_result.verified:
            output["verified"] = True
        if tool_name == "open_path":
            target = Path(str(arguments.get("path") or ""))
            if target.exists():
                output["verified"] = True
                output["path"] = str(target.resolve())
        return ToolResultPayload(
            tool_call_id=f"pc-{tool_name}",
            success=bool(exec_result.success),
            output=output,
            error=exec_result.error,
        )

    return process


@pytest.mark.asyncio
async def test_phase6_full_e2e_flow(settings, desktop, monkeypatch):
    ctx = ConversationalContext()
    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)

    monkeypatch.setattr(
        "hermes.tools.pc_manager.PCManager.process",
        _make_real_pc_process(desktop),
    )
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.save",
        lambda self: None,
    )
    monkeypatch.setattr("hermes.client.session_store.append_conversation_turn", lambda *a, **k: None)

    steps = [
        "Masaüstünde HermesPhase6 klasörü oluştur.",
        "İçine notlar.txt oluştur, Phase 6 test yaz.",
        "Son oluşturduğun dosyayı aç.",
        "İçeriğini Hermes çalışıyor olarak değiştir.",
        "Bu klasördeki txt dosyalarını listele.",
        "Son dosyanın adını üçüncü.txt yap.",
        "Son oluşturduğun dosyayı aç.",
    ]

    for message in steps:
        response = await orchestrator.process_message(message)
        assert "?" not in response or "dogrulayamadim" in response.casefold()

    final_path = desktop / "HermesPhase6" / "üçüncü.txt"
    assert final_path.exists()
    assert final_path.read_text(encoding="utf-8") == "Hermes çalışıyor"
    assert not (desktop / "HermesPhase6" / "notlar.txt").exists()

    summary_turn = handle_meta_conversation("Ne yaptın?", ctx)
    assert summary_turn.handled
    assert is_user_facing_summary(summary_turn.response)
    assert "open_path" not in summary_turn.response
    assert "rename_path" not in summary_turn.response
    assert len(ctx.recent_verified_actions) >= 2
