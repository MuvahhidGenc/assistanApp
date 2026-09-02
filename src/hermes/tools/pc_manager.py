from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from hermes.server.models import ToolCallRequest, ToolResultPayload
from hermes.tools.executor import ToolApprovalRequiredError, ToolExecutor
from hermes.tools.manifest import parse_local_tool_request
from hermes.tools.registry import ToolRegistry

_LOCAL_TOOL_PREFIX = re.compile(r"^LOCAL_TOOL\s+", re.IGNORECASE)


def parse_pc_command(command: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Parse a PC control command into (tool_name, arguments)."""
    if isinstance(command, dict):
        name = str(command.get("name") or command.get("tool") or "").strip()
        args = command.get("arguments") or command.get("args") or {}
        if not name:
            raise ValueError("Command dict must include 'name' or 'tool'.")
        if not isinstance(args, dict):
            raise ValueError("'arguments' must be a JSON object.")
        return name, args

    text = command.strip()
    if not text:
        raise ValueError("Command is empty.")

    if _LOCAL_TOOL_PREFIX.match(text) or text.upper().startswith("LOCAL_TOOL"):
        local = parse_local_tool_request(text)
        if not local:
            raise ValueError("Invalid LOCAL_TOOL command.")
        return local.name, local.arguments

    if text.startswith("{"):
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("JSON command must be an object.")
        return parse_pc_command(payload)

    if " " in text:
        name, arg_text = text.split(" ", 1)
        name = name.strip()
        arg_text = arg_text.strip()
        if arg_text.startswith("{"):
            args = json.loads(arg_text)
        elif name in ("type_text",):
            args = {"text": arg_text}
        else:
            args = {name.split("_")[-1]: arg_text}
        if isinstance(args, dict):
            return name, args
        return name, {}

    return text, {}


@dataclass
class PCManager:
    """Unified PC control entry point: process(command) -> tool result."""

    registry: ToolRegistry
    executor: ToolExecutor

    async def process(
        self,
        command: str | dict[str, Any],
        run_id: str = "",
        skip_approval: bool = False,
        *,
        user_message: str = "",
    ) -> ToolResultPayload:
        tool_name, arguments = parse_pc_command(command)
        tool = self.registry.get(tool_name)
        if not tool:
            raise ValueError(f"Unknown PC tool: {tool_name}")

        execution_target = "client"
        mission_id: str | None = None
        step_id: str | None = None
        if isinstance(command, dict):
            execution_target = str(command.get("execution_target") or "client")
            mission_id = command.get("mission_id")
            step_id = command.get("step_id")

        tool_call = ToolCallRequest(
            id=f"pc-{uuid4().hex[:12]}",
            name=tool_name,
            arguments=arguments,
            risk_level=tool.risk_level.value,
            execution_target=execution_target,
            mission_id=mission_id,
            step_id=step_id,
        )
        try:
            return await self.executor.execute_tool_call(
                tool_call,
                run_id=run_id,
                skip_approval=skip_approval,
                user_message=user_message,
            )
        except ToolApprovalRequiredError:
            raise
