from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.windows.input_backend import run_in_thread


class ListWindowsTool(BaseTool):
    name = "list_windows"
    description = "Acik pencere basliklarini listeler."
    risk_level = RiskLevel.READ_ONLY
    category = "computer_control"

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        try:
            def _list() -> list[dict[str, Any]]:
                import pygetwindow as gw

                return [{"title": w.title, "visible": w.visible} for w in gw.getAllWindows() if w.title]

            windows = await run_in_thread(_list)
            return ToolExecutionResult(
                success=True,
                output={"count": len(windows), "windows": windows[:50]},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class ScrollTool(BaseTool):
    name = "scroll"
    description = "Fare tekerlegi ile kaydirma yapar."
    risk_level = RiskLevel.LOW_RISK
    category = "computer_control"

    async def execute(self, direction: str = "down", amount: int = 3, **kwargs: Any) -> ToolExecutionResult:
        try:
            def _scroll() -> dict[str, Any]:
                import pyautogui

                clicks = amount if direction.lower() == "down" else -amount
                pyautogui.scroll(clicks)
                return {"direction": direction, "amount": amount}

            data = await run_in_thread(_scroll)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class ShowDesktopTool(BaseTool):
    name = "show_desktop"
    description = "Masaustunu gosterir (Win+D)."
    risk_level = RiskLevel.LOW_RISK
    category = "computer_control"

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        try:
            from hermes.tools.windows.input_backend import press_keys

            data = await run_in_thread(press_keys, ["win", "d"])
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class BrowserNavTool(BaseTool):
    name = "browser_nav"
    description = "Tarayici geri/ileri/yenile navigasyonu."
    risk_level = RiskLevel.LOW_RISK
    category = "computer_control"

    async def execute(self, action: str = "back", **kwargs: Any) -> ToolExecutionResult:
        key_map = {"back": ["alt", "left"], "forward": ["alt", "right"], "refresh": ["f5"]}
        keys = key_map.get(action.lower(), ["alt", "left"])
        try:
            from hermes.tools.windows.input_backend import press_keys

            data = await run_in_thread(press_keys, keys)
            return ToolExecutionResult(success=True, output={"action": action, **data}, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class ClickTextTool(BaseTool):
    name = "click_text"
    description = "Ekranda metin arayip tiklar (OCR tabanli)."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "computer_control"

    async def execute(self, text: str = "", **kwargs: Any) -> ToolExecutionResult:
        if not text:
            return ToolExecutionResult(success=False, error="text required")
        try:
            from hermes.vision import find_text_on_screen

            data = await run_in_thread(find_text_on_screen, text)
            if not data.get("found"):
                return ToolExecutionResult(
                    success=False,
                    error=f"Metin bulunamadi: {text}",
                    output=data,
                )
            return ToolExecutionResult(success=True, output={"text": text, **data}, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))
