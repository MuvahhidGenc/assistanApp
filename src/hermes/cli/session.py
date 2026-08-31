from __future__ import annotations

from typing import Any

from rich.console import Console

from hermes.agent.orchestrator import AgentPhase


class CliChatSession:
    def __init__(self, console: Console, debug: bool = False) -> None:
        self.console = console
        self.debug = debug
        self._last_status: str | None = None

    async def on_status(
        self,
        phase: AgentPhase,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if message == self._last_status:
            return
        self._last_status = message
        self.console.print(message)
        if self.debug and extra:
            self.console.print(extra)
