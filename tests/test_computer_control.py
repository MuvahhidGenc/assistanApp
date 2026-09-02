from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.config.settings import RiskLevel
from hermes.security.policy_engine import PolicyDecision, PolicyEngine
from hermes.tools.registry import create_default_registry
from hermes.tools.windows.computer_control import OpenAppTool, OpenUrlTool, ScreenshotTool


def test_registry_includes_computer_control_tools():
    registry = create_default_registry()
    for name in (
        "screenshot",
        "open_app",
        "open_url",
        "focus_window",
        "type_text",
        "press_keys",
        "click",
        "move_mouse",
    ):
        assert name in registry
    assert len(registry) >= 22


def test_screenshot_is_read_only():
    registry = create_default_registry()
    tool = registry.get("screenshot")
    assert tool is not None
    assert tool.risk_level == RiskLevel.READ_ONLY


def test_open_app_is_low_risk():
    registry = create_default_registry()
    assert registry.get("open_app").risk_level == RiskLevel.LOW_RISK
    assert registry.get("open_url").risk_level == RiskLevel.LOW_RISK


def test_interactive_tools_require_approval():
    engine = PolicyEngine()
    for name in ("type_text", "press_keys", "click", "move_mouse"):
        result = engine.evaluate(name, {"text": "hello"} if name == "type_text" else {"keys": ["enter"]} if name == "press_keys" else {"x": 1, "y": 2})
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL


def test_low_risk_tools_allowed_without_approval():
    engine = PolicyEngine()
    for name in ("open_app", "open_url", "focus_window"):
        args = {"app": "chrome"} if name == "open_app" else {"url": "https://google.com"} if name == "open_url" else {"title": "Chrome"}
        result = engine.evaluate(name, args)
        assert result.decision == PolicyDecision.ALLOW
        assert result.risk_level == RiskLevel.LOW_RISK


def test_destructive_type_text_denied():
    engine = PolicyEngine()
    result = engine.evaluate("type_text", {"text": "format c:"})
    assert result.decision == PolicyDecision.DENY


def test_sensitive_field_warning():
    engine = PolicyEngine()
    result = engine.evaluate("type_text", {"text": "abc123", "target_field": "password"})
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert "Sifre/gizli" in result.reason


@pytest.mark.asyncio
async def test_open_app_tool_mocked():
    tool = OpenAppTool()
    with patch(
        "hermes.tools.windows.computer_control.open_application",
        return_value={"app": "chrome", "pid": 99, "reused": True, "verified": True},
    ):
        result = await tool.execute(app="chrome")
    assert result.success
    assert result.output["app"] == "chrome"
    assert result.verified is True


@pytest.mark.asyncio
async def test_open_url_tool_mocked():
    tool = OpenUrlTool()
    with patch(
        "hermes.tools.windows.input_backend.open_url",
        return_value={"url": "https://google.com", "opened": True},
    ):
        result = await tool.execute(url="https://google.com")
    assert result.success
    assert "google.com" in result.output["url"]


@pytest.mark.asyncio
async def test_screenshot_tool_mocked():
    tool = ScreenshotTool()
    with patch(
        "hermes.tools.windows.computer_control.take_screenshot",
        return_value={"path": "C:\\temp\\s.png", "width": 100, "height": 100},
    ):
        result = await tool.execute()
    assert result.success
    assert result.output["width"] == 100
