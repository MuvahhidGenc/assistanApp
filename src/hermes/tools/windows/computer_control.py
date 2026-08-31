from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.windows.input_backend import (
    click_at,
    focus_window,
    move_mouse,
    open_application,
    open_url,
    press_keys,
    run_in_thread,
    take_screenshot,
    type_text,
)


class ScreenshotTool(BaseTool):
    name = "screenshot"
    description = "Ekran goruntusu alir (PNG dosyasi + boyut bilgisi)."
    risk_level = RiskLevel.READ_ONLY
    category = "computer_control"

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        region = kwargs.get("region")
        if isinstance(region, list) and len(region) == 4:
            region = tuple(region)
        try:
            data = await run_in_thread(take_screenshot, region=region)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class OpenAppTool(BaseTool):
    name = "open_app"
    description = "Windows uygulamasini acar (ornek: chrome, notepad, edge)."
    risk_level = RiskLevel.LOW_RISK
    category = "computer_control"

    async def execute(self, app: str = "", args: list[str] | None = None, **kwargs: Any) -> ToolExecutionResult:
        if not app:
            return ToolExecutionResult(success=False, error="app required")
        try:
            data = await run_in_thread(open_application, app, args=args)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App alias or executable name (e.g. chrome)"},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional command-line arguments",
                },
            },
            "required": ["app"],
        }


class OpenUrlTool(BaseTool):
    name = "open_url"
    description = "URL acar (varsayilan tarayici veya belirtilen browser ile)."
    risk_level = RiskLevel.LOW_RISK
    category = "computer_control"

    async def execute(self, url: str = "", browser: str = "", **kwargs: Any) -> ToolExecutionResult:
        if not url:
            return ToolExecutionResult(success=False, error="url required")
        try:
            data = await run_in_thread(open_url, url, browser=browser)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open"},
                "browser": {"type": "string", "description": "Optional browser alias (chrome, edge)"},
            },
            "required": ["url"],
        }


class FocusWindowTool(BaseTool):
    name = "focus_window"
    description = "Basligina gore pencereyi one getirir."
    risk_level = RiskLevel.LOW_RISK
    category = "computer_control"

    async def execute(self, title: str = "", partial: bool = True, **kwargs: Any) -> ToolExecutionResult:
        if not title:
            return ToolExecutionResult(success=False, error="title required")
        try:
            data = await run_in_thread(focus_window, title, partial=partial)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title or substring"},
                "partial": {"type": "boolean", "default": True},
            },
            "required": ["title"],
        }


class TypeTextTool(BaseTool):
    name = "type_text"
    description = "Aktif pencereye metin yazar (onay gerektirir)."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "computer_control"

    async def execute(
        self,
        text: str = "",
        interval: float = 0.02,
        target_field: str = "",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        if not text:
            return ToolExecutionResult(success=False, error="text required")
        try:
            data = await run_in_thread(type_text, text, interval=interval)
            if target_field:
                data["target_field"] = target_field
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "interval": {"type": "number", "default": 0.02},
                "target_field": {
                    "type": "string",
                    "description": "Optional field label (password fields trigger extra warning)",
                },
            },
            "required": ["text"],
        }


class PressKeysTool(BaseTool):
    name = "press_keys"
    description = "Klavye kisayolu veya tus kombinasyonu gonderir (onay gerektirir)."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "computer_control"

    async def execute(self, keys: list[str] | None = None, presses: int = 1, **kwargs: Any) -> ToolExecutionResult:
        key_list = keys or kwargs.get("key") or []
        if isinstance(key_list, str):
            key_list = [key_list]
        if not key_list:
            return ToolExecutionResult(success=False, error="keys required")
        try:
            data = await run_in_thread(press_keys, key_list, presses=presses)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "keys": {"type": "array", "items": {"type": "string"}},
                "presses": {"type": "integer", "default": 1},
            },
            "required": ["keys"],
        }


class ClickTool(BaseTool):
    name = "click"
    description = "Belirtilen ekran koordinatina tiklar (onay gerektirir)."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "computer_control"

    async def execute(
        self,
        x: int = 0,
        y: int = 0,
        button: str = "left",
        clicks: int = 1,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        try:
            data = await run_in_thread(click_at, int(x), int(y), button=button, clicks=clicks)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1},
            },
            "required": ["x", "y"],
        }


class MoveMouseTool(BaseTool):
    name = "move_mouse"
    description = "Fare imlecini belirtilen koordinata tasir (onay gerektirir)."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "computer_control"

    async def execute(self, x: int = 0, y: int = 0, duration: float = 0.2, **kwargs: Any) -> ToolExecutionResult:
        try:
            data = await run_in_thread(move_mouse, int(x), int(y), duration=duration)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "duration": {"type": "number", "default": 0.2},
            },
            "required": ["x", "y"],
        }
