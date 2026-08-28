from __future__ import annotations

from hermes.client.local_context import LocalClientContext
from hermes.server.models import ToolResultPayload
from hermes.tools.manifest import (
    LocalToolRequest,
    build_tool_manifest,
    extract_url_hint,
    format_tool_result_message,
    parse_local_tool_request,
)
from hermes.tools.registry import create_default_registry


def test_build_tool_manifest_has_required_fields():
    registry = create_default_registry()
    manifest = build_tool_manifest(registry)

    assert len(manifest) >= 22
    entry = next(item for item in manifest if item["name"] == "get_system_info")
    assert entry["category"] == "system"
    assert entry["read_only"] is True
    assert entry["input_schema"]["type"] == "object"
    assert "description" in entry


def test_parse_local_tool_request():
    text = 'LOCAL_TOOL {"name": "get_system_info", "arguments": {}}'
    parsed = parse_local_tool_request(text)
    assert parsed == LocalToolRequest(name="get_system_info", arguments={})


def test_parse_local_tool_request_ignores_normal_text():
    assert parse_local_tool_request("Merhaba, sistem bilgisi icin bakiyorum.") is None


def test_format_tool_result_message():
    payload = ToolResultPayload(
        tool_call_id="t1",
        success=True,
        output={"hostname": "PC1"},
    )
    message = format_tool_result_message("get_system_info", payload)
    assert message.startswith("TOOL_RESULT ")
    assert "get_system_info" in message
    assert "PC1" in message


def test_extract_url_hint():
    assert extract_url_hint("google.com'a git") == "https://google.com"
    assert extract_url_hint("https://example.com/path") == "https://example.com/path"


def test_format_tool_result_open_app_navigation_hint():
    payload = ToolResultPayload(tool_call_id="t1", success=True, output={"app": "chrome"})
    message = format_tool_result_message(
        "open_app",
        payload,
        user_message="Chrome ac ve google.com'a git",
    )
    assert "open_url" in message
    assert "google.com" in message
    assert "Ozeti henuz verme" in message


def test_local_client_context_instructions_include_manifest():
    registry = create_default_registry()
    ctx = LocalClientContext(registry)
    instructions = ctx.build_run_instructions()

    assert "LOCAL_TOOL" in instructions
    assert "get_system_info" in instructions
    assert ctx.tool_count >= 22


def test_local_client_context_session_metadata():
    registry = create_default_registry()
    ctx = LocalClientContext(registry, client_name="Test Client")
    metadata = ctx.build_session_metadata()

    assert metadata["platform"] == "windows"
    assert metadata["client"] == "Test Client"
    assert len(metadata["local_tools"]) >= 22
    assert "local_tools_summary" in metadata


def test_capabilities_notice():
    registry = create_default_registry()
    ctx = LocalClientContext(registry)
    notice = ctx.capabilities_notice()
    assert "Windows client" in notice
    assert "focus_window" in notice or "click" in notice
