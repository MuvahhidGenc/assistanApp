from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel  # noqa: F401
from hermes.tools.base import BaseTool, ToolDefinition, ToolExecutionResult  # noqa: F401

def _risk_order(risk: RiskLevel | None) -> int:
    return risk.severity if risk is not None else 99


def _all_windows_tools() -> list[BaseTool]:
    from hermes.tools.windows.computer_control import (
        BrowserNavTool,
        ClickTool,
        ClickTextTool,
        FocusWindowTool,
        MoveMouseTool,
        OpenAppTool,
        OpenUrlTool,
        PressKeysTool,
        ScreenshotTool,
        ScrollTool,
        ShowDesktopTool,
        TypeTextTool,
    )
    from hermes.tools.windows.debug_tools import EchoTool
    from hermes.tools.windows.file_tools import (
        CopyFileTool,
        CreateWordDocumentTool,
        DeletePathTool,
        ListDirectoryTool,
        MoveFileTool,
        ReadFileTool,
        RenamePathTool,
        SearchFilesTool,
        WriteFileTool,
    )
    from hermes.tools.windows.diagnostic_tools import QueryEventLogTool, ReadRegistryTool
    from hermes.tools.windows.network_tools import (
        CheckPortTool,
        DnsLookupTool,
        GetNetworkConfigTool,
        PingHostTool,
        TracerouteTool,
    )
    from hermes.tools.windows.pc_actions import (
        ControlServiceTool,
        CreateFolderTool,
        OpenPathTool,
        DownloadFileTool,
        GetClipboardTool,
        GitCloneTool,
        InstallProgramTool,
        KillProcessTool,
        ListWindowsTool,
        ReadScreenTextTool,
        RunCommandTool,
        SetClipboardTool,
        SetDnsTool,
        SetVolumeTool,
    )
    from hermes.tools.windows.process_tools import (
        GetServiceStatusTool,
        ListProcessesTool,
        ListServicesTool,
    )
    from hermes.tools.windows.system_tools import (
        GetDiskInfoTool,
        GetSystemInfoTool,
        ListInstalledProgramsTool,
    )

    return [
        ClickTool(),
        ClickTextTool(),
        ScrollTool(),
        ShowDesktopTool(),
        BrowserNavTool(),
        FocusWindowTool(),
        MoveMouseTool(),
        OpenAppTool(),
        OpenUrlTool(),
        PressKeysTool(),
        ScreenshotTool(),
        TypeTextTool(),
        EchoTool(),
        QueryEventLogTool(),
        ReadRegistryTool(),
        CheckPortTool(),
        DnsLookupTool(),
        GetNetworkConfigTool(),
        PingHostTool(),
        TracerouteTool(),
        SetDnsTool(),
        ListWindowsTool(),
        SetVolumeTool(),
        GetClipboardTool(),
        SetClipboardTool(),
        KillProcessTool(),
        ControlServiceTool(),
        ReadScreenTextTool(),
        CreateFolderTool(),
        OpenPathTool(),
        InstallProgramTool(),
        DownloadFileTool(),
        GitCloneTool(),
        RunCommandTool(),
        GetServiceStatusTool(),
        ListProcessesTool(),
        ListServicesTool(),
        GetDiskInfoTool(),
        GetSystemInfoTool(),
        ListInstalledProgramsTool(),
        ListDirectoryTool(),
        ReadFileTool(),
        CopyFileTool(),
        MoveFileTool(),
        RenamePathTool(),
        SearchFilesTool(),
        WriteFileTool(),
        CreateWordDocumentTool(),
        DeletePathTool(),
    ]


class ToolRegistry:
    """Registry of locally available Windows tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if not tool.name:
            raise ValueError("Tool must have a name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return [t.get_definition() for t in self._tools.values()]

    def list_by_category(self, category: str) -> list[ToolDefinition]:
        return [d for d in self.list_tools() if d.category == category]

    def categories(self) -> list[str]:
        return sorted({d.category for d in self.list_tools()})

    def find_by_capability(self, capability: str) -> list[ToolDefinition]:
        """Tools that can perform a capability, lowest risk first.

        This is the name-independent lookup: a goal asks for what it needs to
        happen, not for a tool it already knows about.
        """
        wanted = str(capability)
        matches = [d for d in self.list_tools() if wanted in d.capabilities]
        matches.sort(key=lambda d: _risk_order(d.risk_level))
        return matches

    def capabilities(self) -> list[str]:
        found: set[str] = set()
        for definition in self.list_tools():
            found.update(definition.capabilities)
        return sorted(found)

    def capability_map(self) -> dict[str, tuple[str, ...]]:
        return {d.name: d.capabilities for d in self.list_tools()}

    def risk_map(self) -> dict[str, RiskLevel]:
        """Declared risk per tool — the single source of truth for policy."""
        return {d.name: d.risk_level for d in self.list_tools()}

    def to_manifest(self) -> list[dict[str, Any]]:
        from hermes.tools.manifest import build_tool_manifest

        return build_tool_manifest(self)

    def to_capabilities_dict(self) -> dict[str, Any]:
        return {"local_tools": self.to_manifest(), "tool_count": len(self._tools)}

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def create_default_registry() -> ToolRegistry:
    """Register all Windows IT tools."""
    registry = ToolRegistry()
    for tool in _all_windows_tools():
        registry.register(tool)
    return registry
