from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolDefinition, ToolExecutionResult


def _all_windows_tools() -> list[BaseTool]:
    from hermes.tools.windows.computer_control import (
        ClickTool,
        FocusWindowTool,
        MoveMouseTool,
        OpenAppTool,
        OpenUrlTool,
        PressKeysTool,
        ScreenshotTool,
        TypeTextTool,
    )
    from hermes.tools.windows.debug_tools import EchoTool
    from hermes.tools.windows.diagnostic_tools import QueryEventLogTool, ReadRegistryTool
    from hermes.tools.windows.extra_tools import (
        DownloadFileTool,
        GitCloneTool,
        InstallProgramTool,
        ReadScreenTextTool,
        SetDnsTool,
    )
    from hermes.tools.windows.file_tools import (
        CreateFolderTool,
        CreateWordDocumentTool,
        DeletePathTool,
        ListDirectoryTool,
        WriteFileTool,
    )
    from hermes.tools.windows.interaction_tools import (
        BrowserNavTool,
        ClickTextTool,
        ListWindowsTool,
        ScrollTool,
        ShowDesktopTool,
    )
    from hermes.tools.windows.network_tools import (
        CheckPortTool,
        DnsLookupTool,
        GetNetworkConfigTool,
        PingHostTool,
        TracerouteTool,
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
        GetSystemInfoTool(),
        GetDiskInfoTool(),
        ListInstalledProgramsTool(),
        GetNetworkConfigTool(),
        SetDnsTool(),
        PingHostTool(),
        DnsLookupTool(),
        TracerouteTool(),
        CheckPortTool(),
        DownloadFileTool(),
        GitCloneTool(),
        ListProcessesTool(),
        ListServicesTool(),
        GetServiceStatusTool(),
        QueryEventLogTool(),
        ReadRegistryTool(),
        InstallProgramTool(),
        EchoTool(),
        ScreenshotTool(),
        ReadScreenTextTool(),
        OpenAppTool(),
        OpenUrlTool(),
        FocusWindowTool(),
        TypeTextTool(),
        PressKeysTool(),
        ClickTool(),
        MoveMouseTool(),
        ListWindowsTool(),
        ScrollTool(),
        ShowDesktopTool(),
        BrowserNavTool(),
        ClickTextTool(),
        WriteFileTool(),
        ListDirectoryTool(),
        DeletePathTool(),
        CreateFolderTool(),
        CreateWordDocumentTool(),
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

    def to_manifest(self) -> list[dict[str, Any]]:
        from hermes.tools.manifest import build_tool_manifest

        return build_tool_manifest(self)

    def to_capabilities_dict(self) -> dict[str, Any]:
        return {
            "local_tools": self.to_manifest(),
            "tool_count": len(self._tools),
        }

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
