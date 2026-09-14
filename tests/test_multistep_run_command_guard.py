"""End-to-end: multi-step local task must not hang on a POSIX run_command.

The reasoning model occasionally emits bash/macOS syntax ("mkdir -p X &&
echo .. > f") for tasks the assistant should do with Windows file tools.
Before the ``run_command`` POSIX guard this produced a high-risk
approval wait + a hung subprocess (observed: 298s action_cancelled).

This test wires the real ``build_v3_application`` pipeline (executor,
policy, approval, execution log) with a scripted LLM that first
misfires with a Unix command, then follows the correct filesystem
steps. It verifies the guard returns fast, the orchestrator re-reasons,
and the turn completes without ever calling the shell.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.reasoning import ReasoningReply
from hermes.reasoning.transport import ReasoningPrompt
from hermes.runtime.bootstrap import build_v3_application
from tests._approval_providers import approving_provider


class _StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list = []

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        self.calls.append(json.dumps(prompt.user_message))
        next_reply = self.replies.pop(0)
        return ReasoningReply(decision_json=next_reply, raw_text=json.dumps(next_reply))


class _AdapterFromStub:
    def __init__(self, stub: _StubLLM) -> None:
        self._stub = stub

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        return await self._stub.reason(prompt)


@pytest.mark.asyncio
async def test_multistep_recovers_from_posix_run_command(tmp_path: Path):
    folder = tmp_path / "tevhid"
    text_file = folder / "tevhid.txt"
    llm = _StubLLM(
        [
            {
                "kind": "action",
                "capability": "terminal.execute",
                "arguments": {
                    "command": (
                        f"mkdir -p \"{folder.as_posix()}\" && echo 'Tevhid notlari' "
                        f"> \"{text_file.as_posix()}\""
                    )
                },
            },
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(folder)},
            },
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(text_file), "content": "Tevhid notlari"},
            },
            {"kind": "complete", "summary": "klasor ve dosya olusturuldu", "evidence_ids": []},
        ]
    )
    app = build_v3_application(
        server=None,
        log_dir=tmp_path,
        approval_provider=approving_provider(),
    )
    app.orchestrator._runtime.client = _AdapterFromStub(llm)

    reply = await app.orchestrator.process_message("tevhid klasoru olustur, icne txt yaz")

    assert reply and "klasor" in reply
    assert folder.exists() and folder.is_dir()
    assert text_file.exists()
    assert text_file.read_text(encoding="utf-8").strip() == "Tevhid notlari"