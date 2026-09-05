from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.local_verify import validate_write_parent_directory, verify_write_file_result
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.agent.user_messages import format_write_file_message
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.server.models import ToolResultPayload
from hermes.agent.local_intent import LocalIntent, LocalToolRequest


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


def test_sequential_folder_writes_stay_under_hermes(resolver, tmp_path, monkeypatch):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: folder.resolve(),
    )

    ctx = ConversationalContext(
        active_folder=str(folder.resolve()),
        last_created_folder=str(folder.resolve()),
    )

    msg_a = "Hermes klasörünün içine a.txt oluştur ve içine A yaz."
    result_a = resolver.resolve(msg_a, ctx)
    assert result_a.intent.request.arguments["path"].endswith("Hermes\\a.txt")

    ctx.active_folder = str(folder.resolve())
    ctx.last_created_folder = str(folder.resolve())

    msg_b = "Son oluşturduğun klasörün içine b.txt oluştur ve içine B yaz."
    result_b = resolver.resolve(msg_b, ctx)
    path_b = Path(result_b.intent.request.arguments["path"])
    assert path_b.parent.resolve() == folder.resolve()
    assert path_b.name == "b.txt"
    assert path_b.parent.name == "Hermes"
    assert "Desktop\\b.txt" != str(path_b)

    msg_c = "Bu klasöre c.txt oluştur ve içine C yaz."
    result_c = resolver.resolve(msg_c, ctx)
    path_c = Path(result_c.intent.request.arguments["path"])
    assert path_c.parent.resolve() == folder.resolve()
    assert path_c.name == "c.txt"


def test_validate_write_parent_rejects_desktop_fallback(tmp_path):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    ok, reason = validate_write_parent_directory(
        str(tmp_path / "Desktop" / "b.txt"),
        str(folder),
    )
    assert ok is False
    assert "Hermes" in reason or "beklenen" in reason


def test_format_write_file_message_is_natural():
    intent = LocalIntent(
        LocalToolRequest("write_file", {"path": r"C:\Desktop\Hermes\ikinci.txt", "content": "12345"}),
        summary="x",
    )
    result = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"path": r"C:\Desktop\Hermes\ikinci.txt", "size": 5, "verified": True},
    )
    text = format_write_file_message(intent, result)
    assert "12345" in text
    assert "Ikinci" in text or "ikinci" in text.casefold()
    assert "C:\\" not in text
    assert "byte" not in text


@pytest.mark.asyncio
async def test_verify_write_file_result_checks_content(tmp_path):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    target = folder / "ikinci.txt"
    target.write_text("12345", encoding="utf-8")

    raw = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"path": str(target), "size": 5},
    )
    verified = await verify_write_file_result(
        raw,
        {"path": str(target), "content": "12345"},
    )
    assert verified.success is True
    assert verified.output["verified"] is True


@pytest.mark.asyncio
async def test_verify_write_file_fails_on_wrong_content(tmp_path):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    target = folder / "x.txt"
    target.write_text("WRONG", encoding="utf-8")

    raw = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"path": str(target), "size": 5},
    )
    verified = await verify_write_file_result(
        raw,
        {"path": str(target), "content": "ABC"},
    )
    assert verified.success is False
    assert verified.output.get("verification_failed") is True


@pytest.mark.asyncio
async def test_write_file_single_execution_and_status(
    settings: AppSettings, tmp_path, monkeypatch
):
    """write_file must run once; EXECUTING status must not duplicate."""
    from hermes.agent.orchestrator import AgentPhase
    from hermes.agent.local_intent import LocalIntent, LocalToolRequest

    folder = tmp_path / "Hermes"
    folder.mkdir()
    target = folder / "b.txt"

    ctx = ConversationalContext(
        active_folder=str(folder.resolve()),
        last_created_folder=str(folder.resolve()),
    )

    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)
    status_messages: list[tuple[str, str]] = []
    write_calls = 0

    async def capture_status(phase, message, extra=None):
        status_messages.append((phase.value, message))

    orchestrator._on_status = capture_status

    async def fake_pc_process(self, command, run_id="", skip_approval=False, *, user_message="", verify=True):
        nonlocal write_calls
        name = command["name"] if isinstance(command, dict) else ""
        if name == "write_file":
            write_calls += 1
            args = command["arguments"]
            Path(str(args["path"])).write_text(str(args["content"]), encoding="utf-8")
            return ToolResultPayload(
                tool_call_id="1",
                success=True,
                output={"path": str(args["path"]), "size": len(str(args["content"]))},
            )
        return ToolResultPayload(tool_call_id="x", success=False, error="unexpected tool")

    monkeypatch.setattr(
        "hermes.tools.pc_manager.PCManager.process",
        fake_pc_process,
    )

    intent = LocalIntent(
        LocalToolRequest("write_file", {"path": str(target), "content": "B"}),
        summary="Dosya olusturulacak",
    )
    await orchestrator._execute_resolved_local_intent(
        intent,
        "Son olusturdugun klasorun icine b.txt olustur ve icine B yaz.",
        ctx,
        resolved_references={"target_folder": str(folder.resolve())},
    )

    assert write_calls == 1
    assert target.read_text(encoding="utf-8") == "B"
    executing_write_msgs = [
        msg
        for phase, msg in status_messages
        if phase == AgentPhase.EXECUTING.value and "olustur" in msg.casefold()
    ]
    assert len(executing_write_msgs) == 1
    verifying_msgs = [msg for phase, msg in status_messages if phase == AgentPhase.VERIFYING.value]
    assert any("kontrol" in msg.casefold() for msg in verifying_msgs)


@pytest.mark.asyncio
async def test_orchestrator_resolved_write_verifies_before_success(
    settings: AppSettings, tmp_path, monkeypatch
):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    target = folder / "ikinci.txt"

    ctx = ConversationalContext(
        active_folder=str(folder.resolve()),
        last_created_folder=str(folder.resolve()),
    )

    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)
    phases: list[str] = []

    async def capture_status(phase, message, extra=None):
        phases.append(phase.value)

    orchestrator._on_status = capture_status

    async def fake_execute(local_call, run_id, *, user_message=""):
        target.write_text("12345", encoding="utf-8")
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"path": str(target), "size": 5},
        )

    orchestrator._execute_local_tool = fake_execute  # type: ignore[method-assign]

    message = "Son oluşturduğun klasörün içine ikinci.txt oluştur ve içine 12345 yaz."

    with monkeypatch.context() as mp:
        mp.setattr(
            "hermes.context.conversational_context.ConversationalContext.load",
            lambda: ctx,
        )
        mp.setattr("hermes.context.conversational_context.ConversationalContext.save", lambda self: None)
        mp.setattr("hermes.client.session_store.append_conversation_turn", lambda *a, **k: None)

        text = await orchestrator.process_message(message)

    server.create_run.assert_not_called()
    assert "verifying" in phases
    assert phases.index("verifying") < phases.index("completed")
    assert "12345" in text
    assert "Ikinci" in text or "ikinci" in text.casefold()
    assert ctx.active_file is not None
    assert Path(ctx.active_file).name == "ikinci.txt"


def test_no_success_message_without_verification_flag():
    intent = LocalIntent(
        LocalToolRequest("write_file", {"path": "x.txt", "content": "a"}),
        summary="x",
    )
    result = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"path": "x.txt", "verification_failed": True},
    )
    text = format_write_file_message(intent, result)
    assert "olusturdum" not in text.casefold()
    assert "dogrulayamadim" in text.casefold()
