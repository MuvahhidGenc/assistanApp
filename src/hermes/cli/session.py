from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel

from hermes.agent.orchestrator import AgentPhase, AgentOrchestrator
from hermes.server.models import ApprovalRequest


class CliChatSession:
    """Shared CLI handlers for chat and interactive modes."""

    def __init__(self, console: Console, *, debug: bool = False) -> None:
        self._console = console
        self._debug = debug
        self._last_status: str | None = None

    async def on_status(self, phase: AgentPhase, message: str, extra: dict[str, Any]) -> None:
        if self._debug:
            payload = {"phase": phase.value, "message": message, **extra}
            self._console.print(f"[dim cyan]{json.dumps(payload, ensure_ascii=False)}[/dim cyan]")
        if not message or message == self._last_status:
            return
        self._last_status = message
        self._console.print(f"[dim]{message}[/dim]")

    async def on_approval(self, approval: ApprovalRequest) -> str:
        import typer

        self._console.print(Panel(approval.title, subtitle=approval.description or ""))
        if approval.plan_steps:
            for i, step in enumerate(approval.plan_steps, 1):
                self._console.print(f"  {i}. {step}")
        return typer.prompt("Yanıtınız")

    def attach(self, agent: AgentOrchestrator) -> None:
        agent._on_status = self.on_status  # noqa: SLF001
        agent._on_approval_required = self.on_approval  # noqa: SLF001

    def reset_status(self) -> None:
        self._last_status = None
