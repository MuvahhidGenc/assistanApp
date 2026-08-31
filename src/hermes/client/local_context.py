from __future__ import annotations

import json
import socket
from typing import Any

from hermes.client.session_store import get_client_id
from hermes.tools.manifest import build_tool_manifest
from hermes.tools.registry import ToolRegistry


class LocalClientContext:
    """Builds session metadata and run instructions for Windows local tools."""

    def __init__(self, registry: ToolRegistry, *, client_name: str = "HERMES Windows Client") -> None:
        self._registry = registry
        self._client_name = client_name
        self._manifest = build_tool_manifest(registry)

    @property
    def manifest(self) -> list[dict[str, Any]]:
        return list(self._manifest)

    @property
    def tool_count(self) -> int:
        return len(self._manifest)

    def _hostname(self) -> str:
        try:
            return socket.gethostname()
        except OSError:
            return "windows"

    def build_session_metadata(self) -> dict[str, Any]:
        return {
            "client": self._client_name,
            "platform": "windows",
            "hostname": self._hostname(),
            "windows_client_id": get_client_id(),
            "local_tools_connected": True,
            "local_tools": self._manifest,
            "local_tools_summary": (
                f"Windows client bagli — {self.tool_count} yerel tool aktif "
                "(PC kontrol, sistem, network, computer control)."
            ),
            "capabilities": {
                "computer_control": True,
                "system_info": True,
                "network_read": True,
                "interactive_input": True,
            },
        }

    def build_run_instructions(self) -> str:
        compact = json.dumps(self._manifest, ensure_ascii=False, indent=2)
        short_hint = (
            "Keep all user-facing replies brief: 2-4 sentences in Turkish unless the user asks for detail."
        )
        pc_tools = ", ".join(
            sorted(entry["name"] for entry in self._manifest if entry.get("category") == "computer_control")
        )
        network_tools = ", ".join(
            sorted(entry["name"] for entry in self._manifest if entry.get("category") == "network")
        )
        system_tools = ", ".join(
            sorted(entry["name"] for entry in self._manifest if entry.get("category") == "system")
        )
        return f"""You are Hermes AI connected to the HERMES Windows Client on the user's PC.

## CRITICAL — Windows client is connected
This session HAS a live Windows client. LOCAL_TOOL commands WILL execute on the user's PC.
Do NOT say "VPS terminalindeyim" or "local tools bağlı değil" — that is wrong for this session.
Always use LOCAL_TOOL for PC/system/network/computer tasks.

## Response style
{short_hint}

## Windows local tools (client-side only)
These {self.tool_count} tools run on the user's Windows machine via policy + approval.
They do NOT execute on the Hermes server/VPS.

To invoke a local tool, respond with EXACTLY one line and nothing else:
LOCAL_TOOL {{"name": "<tool_name>", "arguments": {{<json object>}}}}

When the user message starts with TOOL_RESULT, summarize the tool output for the user in Turkish (2-4 sentences).
Do not invent hardware, OS, network, or registry data — always use local tools first.

### Available tool groups
- System: {system_tools or "get_system_info, get_disk_info, ..."}
- Network/IP: {network_tools or "get_network_config, ping_host, dns_lookup, ..."}
- Computer control: {pc_tools or "screenshot, open_app, type_text, click, ..."}

### Tool manifest
{compact}

### Examples
- System info → LOCAL_TOOL {{"name": "get_system_info", "arguments": {{}}}}
- IP / network → LOCAL_TOOL {{"name": "get_network_config", "arguments": {{}}}}
- DNS change → LOCAL_TOOL {{"name": "set_dns", "arguments": {{"preset": "google"}}}}
- Screenshot → LOCAL_TOOL {{"name": "screenshot", "arguments": {{}}}}
- Download file → LOCAL_TOOL {{"name": "download_file", "arguments": {{"url": "...", "path": "..."}}}}
- Git clone → LOCAL_TOOL {{"name": "git_clone", "arguments": {{"url": "...", "path": "..."}}}}
- Open app → LOCAL_TOOL {{"name": "open_app", "arguments": {{"app": "chrome"}}}}
- Open URL → LOCAL_TOOL {{"name": "open_url", "arguments": {{"url": "https://..."}}}}
- Type text → LOCAL_TOOL {{"name": "type_text", "arguments": {{"text": "hello"}}}}
- Click → LOCAL_TOOL {{"name": "click", "arguments": {{"x": 100, "y": 200}}}}
- Multi-step: ONE tool per response; after TOOL_RESULT continue with next step.
- Do not only open Chrome for every request — pick the correct tool for the task.
"""

    def capabilities_notice(self) -> str:
        names = ", ".join(sorted(entry["name"] for entry in self._manifest)[:8])
        suffix = "..." if self.tool_count > 8 else ""
        return (
            f"Bu Windows client su local tools'a sahip ({self.tool_count}): "
            f"{names}{suffix}"
        )
