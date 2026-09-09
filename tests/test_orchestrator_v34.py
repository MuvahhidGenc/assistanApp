"""V3.4 production-orchestrator acceptance tests."""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from hermes.capability import create_default_capability_registry
from hermes.context.system_paths import current_user_known_folders
from hermes.execution_log import EventKind, ExecutionLogStore
from hermes.reasoning import ReasoningPrompt, ReasoningReply, ReasoningRuntime
from hermes.runtime.executor import V3Executor
from hermes.runtime.bootstrap import build_v3_application
from hermes.runtime.orchestrator import V3Orchestrator
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.base import (
    BaseVerifier,
    VerificationResult,
    VerificationStatus,
)
from hermes.tools.verifiers.registry import create_default_verifier_registry
from hermes.world_model import EvidenceSource
from tests._approval_providers import approving_provider


class _ScriptedClient:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.calls: list[ReasoningPrompt] = []

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        self.calls.append(prompt)
        reply = self.replies.pop(0)
        return ReasoningReply(decision_json=reply, raw_text=json.dumps(reply))


def _build(
    tmp_path: Path,
    client: Any,
    *,
    verifier_registry: Any | None = None,
    turn_timeout_seconds: float = 2.0,
) -> tuple[V3Orchestrator, ExecutionLogStore]:
    tools = create_default_registry()
    capabilities = create_default_capability_registry(tools)
    verifiers = verifier_registry or create_default_verifier_registry()
    approval = ApprovalManager()
    approval.set_handler(approving_provider())
    log = ExecutionLogStore(path=tmp_path / "execution.jsonl")
    tool_executor = ToolExecutor(
        tools,
        PolicyEngine(),
        AuditLogger(log_path=str(tmp_path / "audit.log")),
        approval,
        verifiers,
    )
    executor = V3Executor.from_defaults(
        execution_log=log,
        tool_executor=tool_executor,
        approval_manager=approval,
        tool_registry=tools,
        capability_registry=capabilities,
        verifier_registry=verifiers,
    )
    runtime = ReasoningRuntime(
        client=client,
        capability_registry=capabilities,
        execution_log=log,
    )
    return (
        V3Orchestrator(
            runtime=runtime,
            executor=executor,
            execution_log=log,
            turn_timeout_seconds=turn_timeout_seconds,
        ),
        log,
    )


@pytest.mark.asyncio
async def test_create_folder_verifies_and_next_turn_uses_reference(
    tmp_path: Path,
):
    folder = tmp_path / "deneme12"
    document = folder / "tevhid.docx"

    class _ContinuityClient:
        def __init__(self) -> None:
            self.call_count = 0
            self.second_turn_reference = ""

        async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
            self.call_count += 1
            if self.call_count == 1:
                reply = {
                    "kind": "action",
                    "capability": "filesystem.write",
                    "arguments": {"path": str(folder)},
                    "required_capabilities": ["filesystem.write"],
                }
            elif self.call_count == 2:
                reply = {
                    "kind": "complete",
                    "summary": "folder verified",
                    "evidence_ids": [],
                    "required_capabilities": ["filesystem.write"],
                }
            elif self.call_count == 3:
                reference = prompt.world_snapshot["references"]["last_folder"]
                self.second_turn_reference = reference["value"]
                reply = {
                    "kind": "action",
                    "capability": "document.create",
                    "arguments": {
                        "path": str(Path(reference["value"]) / document.name),
                        "content": "Tevhid hakkında kısa makale.",
                    },
                    "required_capabilities": ["document.create"],
                }
            else:
                reply = {
                    "kind": "complete",
                    "summary": "document verified",
                    "evidence_ids": [],
                    "required_capabilities": ["document.create"],
                }
            return ReasoningReply(decision_json=reply, raw_text=json.dumps(reply))

    client = _ContinuityClient()
    orchestrator, log = _build(tmp_path, client)

    first = await orchestrator.process_turn("Masaüstünde deneme12 klasörü oluştur.")
    second = await orchestrator.process_turn("İçine Word dosyası oluştur.")

    assert first.completed is True
    assert second.completed is True
    assert client.second_turn_reference == str(folder)
    assert document.is_file()
    assert orchestrator.world_model.references["last_file"].value == str(document)
    action_events = [
        event for event in log.all() if event.kind is EventKind.ACTION_STARTED
    ]
    assert len(action_events) == 2


@pytest.mark.asyncio
async def test_production_transport_carries_verified_reference_to_next_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    folder = desktop / "deneme12"
    document = folder / "tevhid.docx"
    monkeypatch.setattr(
        "hermes.context.system_paths.current_user_known_folders",
        lambda: {"desktop": str(desktop)},
    )

    class _Server:
        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def chat(self, request: Any) -> dict[str, Any]:
            self.requests.append(request)
            payload = json.loads(request.message)
            call_number = len(self.requests)
            if call_number == 1:
                discovered = payload["environment"]["extra"][
                    "known_folders"
                ]["desktop"]
                reply = {
                    "kind": "action",
                    "capability": "filesystem.write",
                    "arguments": {"path": str(Path(discovered) / folder.name)},
                    "required_capabilities": ["filesystem.write"],
                }
            elif call_number == 2:
                reply = {
                    "kind": "complete",
                    "summary": "folder verified",
                    "evidence_ids": [],
                    "required_capabilities": ["filesystem.write"],
                }
            elif call_number == 3:
                reference = payload["verified_references"]["last_folder"]
                reply = {
                    "kind": "action",
                    "capability": "document.create",
                    "arguments": {
                        "path": str(Path(reference["value"]) / document.name),
                        "content": "Tevhid hakkında makale.",
                    },
                    "required_capabilities": ["document.create"],
                }
            else:
                reply = {
                    "kind": "complete",
                    "summary": "document verified",
                    "evidence_ids": [],
                    "required_capabilities": ["document.create"],
                }
            return {
                "choices": [{"message": {"content": json.dumps(reply)}}]
            }

    server = _Server()
    app = build_v3_application(
        server=server,
        log_dir=tmp_path / "state",
        approval_provider=approving_provider(),
    )

    first = await app.orchestrator.process_turn(
        "Masaüstünde deneme12 adlı klasör oluştur."
    )
    second = await app.orchestrator.process_turn(
        "Tamam, içine Word dosyası oluştur ve tevhid hakkında makale yaz."
    )

    assert first.completed is True
    assert second.completed is True
    assert folder.is_dir()
    assert document.is_file()
    with zipfile.ZipFile(document) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "Tevhid hakkında makale." in document_xml
    assert server.requests[0].response_format == {"type": "json_object"}
    second_turn_payload = json.loads(server.requests[2].message)
    assert (
        second_turn_payload["verified_references"]["last_folder"]["value"]
        == str(folder)
    )
    assert second_turn_payload["current_evidence"] == []
    assert second_turn_payload["completion_eligible_evidence_ids"] == []
    assert second_turn_payload["turn"]["current_requirements"] == []
    verifications = [
        event
        for event in app.execution_log.all()
        if event.kind is EventKind.VERIFICATION_RECORDED
    ]
    assert [event.payload.status for event in verifications] == [
        "verified",
        "verified",
    ]
    assert app.world_model.task.status == "complete"
    assert all(
        requirement.satisfied for requirement in app.world_model.task.requirements
    )


class _UnknownWriteVerifier(BaseVerifier):
    tool_names = ("write_file",)

    async def verify(self, ctx: Any) -> VerificationResult:
        return VerificationResult(
            status=VerificationStatus.UNKNOWN,
            method="forced_unknown",
            details={"reason": "test uncertainty"},
        )


class _FailedWriteVerifier(BaseVerifier):
    tool_names = ("write_file",)

    async def verify(self, ctx: Any) -> VerificationResult:
        return VerificationResult(
            status=VerificationStatus.FAILED,
            method="forced_failed",
            details={"reason": "test contradiction"},
        )


@pytest.mark.asyncio
async def test_success_true_with_unknown_verification_cannot_complete(
    tmp_path: Path,
):
    verifiers = create_default_verifier_registry()
    verifiers.register(_UnknownWriteVerifier())
    target = tmp_path / "unknown.txt"
    client = _ScriptedClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "written"},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "created",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "user_question",
                "question": "Doğrulama belirsiz; tekrar deneyelim mi?",
                "required_capabilities": ["filesystem.write"],
            },
        ]
    )
    orchestrator, log = _build(
        tmp_path,
        client,
        verifier_registry=verifiers,
    )

    outcome = await orchestrator.process_turn("Dosyayı oluştur.")

    assert target.is_file()
    assert outcome.completed is False
    assert orchestrator.world_model.task.status != "complete"
    verification = [
        event for event in log.all() if event.kind is EventKind.VERIFICATION_RECORDED
    ][-1]
    assert verification.payload.status == "unknown"


@pytest.mark.asyncio
async def test_verification_failure_is_sent_to_rereasoning(tmp_path: Path):
    verifiers = create_default_verifier_registry()
    verifiers.register(_FailedWriteVerifier())
    client = _ScriptedClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "failed.txt"), "content": "x"},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "user_question",
                "question": "Doğrulama çelişti; gözlemleyeyim mi?",
                "required_capabilities": ["filesystem.write"],
            },
        ]
    )
    orchestrator, _ = _build(tmp_path, client, verifier_registry=verifiers)

    outcome = await orchestrator.process_turn("Dosyayı oluştur.")

    assert outcome.completed is False
    assert "verification was failed" in client.calls[1].extra["re_reason"]


@pytest.mark.asyncio
async def test_wrong_path_observation_then_recovery(tmp_path: Path):
    actual = tmp_path / "actual.txt"
    actual.write_text("ground truth", encoding="utf-8")
    client = _ScriptedClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.read",
                "arguments": {"path": str(tmp_path / "wrong.txt")},
                "required_capabilities": ["filesystem.read"],
            },
            {
                "kind": "observation_request",
                "observation_type": "filesystem.list",
                "target": str(tmp_path),
                "required_capabilities": ["filesystem.read"],
            },
            {
                "kind": "action",
                "capability": "filesystem.read",
                "arguments": {"path": str(actual)},
                "required_capabilities": ["filesystem.read"],
            },
            {
                "kind": "complete",
                "summary": "read verified",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.read"],
            },
        ]
    )
    orchestrator, log = _build(tmp_path, client)

    outcome = await orchestrator.process_turn("Dosyayı oku.")

    assert outcome.completed is True
    assert any(
        event.kind is EventKind.OBSERVATION_RECORDED for event in log.all()
    )
    assert client.calls[1].extra["re_reason"]


@pytest.mark.asyncio
async def test_incomplete_multi_step_goal_cannot_complete(tmp_path: Path):
    target = tmp_path / "found.txt"
    target.write_text("x", encoding="utf-8")
    required = ["filesystem.search", "filesystem.open"]
    client = _ScriptedClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.search",
                "arguments": {"path": str(tmp_path), "pattern": "*.txt"},
                "required_capabilities": required,
            },
            {
                "kind": "complete",
                "summary": "all done",
                "evidence_ids": [],
                "required_capabilities": required,
            },
            {
                "kind": "user_question",
                "question": "Dosya bulundu fakat henüz açılmadı.",
                "required_capabilities": required,
            },
        ]
    )
    orchestrator, _ = _build(tmp_path, client)

    outcome = await orchestrator.process_turn("Dosyayı bul ve aç.")

    assert outcome.completed is False
    assert [item.capability for item in orchestrator.world_model.task.unsatisfied] == [
        "filesystem.open"
    ]


@pytest.mark.asyncio
async def test_repeated_capability_requirements_need_distinct_verified_actions(
    tmp_path: Path,
):
    required = ["filesystem.write", "filesystem.write"]
    client = _ScriptedClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "one.txt"), "content": "one"},
                "required_capabilities": required,
            },
            {
                "kind": "complete",
                "summary": "both created",
                "evidence_ids": [],
                "required_capabilities": required,
            },
            {
                "kind": "user_question",
                "question": "İkinci dosya henüz oluşturulmadı.",
                "required_capabilities": required,
            },
        ]
    )
    orchestrator, _ = _build(tmp_path, client)

    outcome = await orchestrator.process_turn("İki dosya oluştur.")

    assert outcome.completed is False
    assert len(orchestrator.world_model.task.unsatisfied) == 1
    assert orchestrator.world_model.task.unsatisfied[0].capability == "filesystem.write"


@pytest.mark.asyncio
async def test_prior_turn_evidence_cannot_complete_unrelated_turn(tmp_path: Path):
    client = _ScriptedClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "first.txt"), "content": "x"},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "first done",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.write"],
            },
        ]
    )
    orchestrator, _ = _build(tmp_path, client)
    first = await orchestrator.process_turn("İlk dosyayı oluştur.")
    assert first.completed is True
    stale_id = next(
        evidence.evidence_id
        for evidence in orchestrator.world_model.evidence
        if evidence.source is EvidenceSource.VERIFIER
    )
    client.replies.extend(
        [
            {
                "kind": "complete",
                "summary": "second done",
                "evidence_ids": [stale_id],
                "required_capabilities": [],
            },
            {
                "kind": "user_question",
                "question": "Yeni görev için kanıt yok.",
                "required_capabilities": [],
            },
        ]
    )

    second = await orchestrator.process_turn("İlgisiz ikinci görevi tamamla.")

    assert second.completed is False
    assert "kanıt yok" in second.reply


@pytest.mark.asyncio
async def test_timeout_cancels_execution_and_next_turn_is_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    client = _ScriptedClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "slow.txt"), "content": "x"},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "next turn clean",
                "evidence_ids": [],
                "required_capabilities": [],
            },
        ]
    )
    orchestrator, log = _build(tmp_path, client, turn_timeout_seconds=0.05)
    tool = orchestrator._executor.tool_registry.get("write_file")
    assert tool is not None

    async def _slow_execute(self: Any, **kwargs: Any) -> Any:
        from hermes.tools.base import ToolExecutionResult

        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ToolExecutionResult(success=True)

    monkeypatch.setattr(type(tool), "execute", _slow_execute)

    timed_out = await orchestrator.process_turn("Yavaş dosyayı oluştur.")
    clean = await orchestrator.process_turn("Yeni görev.")

    assert started.is_set()
    assert cancelled.is_set()
    assert timed_out.completed is False
    assert clean.completed is True
    assert orchestrator._active_turn_task is None
    finished = [
        event for event in log.all() if event.kind is EventKind.ACTION_FINISHED
    ]
    assert finished[-1].payload.error == "action_cancelled"


@pytest.mark.asyncio
async def test_explicit_cancellation_joins_active_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    started = asyncio.Event()
    client = _ScriptedClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "cancel.txt"), "content": "x"},
                "required_capabilities": ["filesystem.write"],
            }
        ]
    )
    orchestrator, log = _build(tmp_path, client)
    tool = orchestrator._executor.tool_registry.get("write_file")
    assert tool is not None

    async def _wait_forever(self: Any, **kwargs: Any) -> Any:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(type(tool), "execute", _wait_forever)
    outer = asyncio.create_task(orchestrator.process_turn("İptal et."))
    await started.wait()

    assert await orchestrator.cancel_active_turn() is True
    with pytest.raises(asyncio.CancelledError):
        await outer

    await asyncio.sleep(0)
    assert orchestrator._active_turn_task is None
    assert [
        event for event in log.all() if event.kind is EventKind.ACTION_FINISHED
    ][-1].payload.error == "action_cancelled"
    assert orchestrator.world_model.task.status == "abandoned"


def test_windows_known_folder_observation_uses_current_user_environment():
    folders = current_user_known_folders()

    assert "desktop" in folders
    assert Path(folders["desktop"]).is_absolute()
    assert "C:\\Users\\OMER\\Desktop" not in folders.values()
    assert "C:\\Users\\Public\\Desktop" not in folders.values()


@pytest.mark.asyncio
async def test_relative_folder_path_anchors_to_observed_desktop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    desktop = tmp_path / "RedirectedDesktop"
    desktop.mkdir()
    monkeypatch.setattr(
        "hermes.context.system_paths.current_user_desktop_path",
        lambda: desktop,
    )
    from hermes.tools.windows.pc_actions import CreateFolderTool

    result = await CreateFolderTool().execute(path="deneme12")

    assert result.success is True
    assert result.output["path"] == str(desktop / "deneme12")
    assert (desktop / "deneme12").is_dir()


def test_world_model_records_distinct_evidence_sources(tmp_path: Path):
    client = _ScriptedClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "evidence.txt"), "content": "x"},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "done",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.write"],
            },
        ]
    )
    orchestrator, log = _build(tmp_path, client)

    asyncio.run(orchestrator.process_turn("Kanıtlı dosya oluştur."))

    assert {evidence.source for evidence in orchestrator.world_model.evidence} == {
        EvidenceSource.TOOL_REPORT,
        EvidenceSource.OBSERVATION,
        EvidenceSource.VERIFIER,
    }
    assert [event.kind for event in log.all()] == [
        EventKind.ACTION_STARTED,
        EventKind.ACTION_FINISHED,
        EventKind.OBSERVATION_RECORDED,
        EventKind.VERIFICATION_RECORDED,
    ]
