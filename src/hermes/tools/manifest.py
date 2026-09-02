from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import ToolRegistry

_TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "echo": {
        "type": "object",
        "properties": {"message": {"type": "string", "description": "Echo message"}},
    },
    "ping_host": {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Host or IP to ping"},
            "count": {"type": "integer", "minimum": 1, "maximum": 10, "default": 4},
        },
        "required": ["host"],
    },
    "dns_lookup": {
        "type": "object",
        "properties": {"hostname": {"type": "string", "description": "Hostname to resolve"}},
        "required": ["hostname"],
    },
    "traceroute": {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Target host"},
            "max_hops": {"type": "integer", "minimum": 1, "maximum": 30, "default": 20},
        },
        "required": ["host"],
    },
    "check_port": {
        "type": "object",
        "properties": {
            "host": {"type": "string"},
            "port": {"type": "integer"},
            "timeout_seconds": {"type": "integer", "default": 5},
        },
        "required": ["host", "port"],
    },
    "list_processes": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "sort_by": {"type": "string", "enum": ["cpu", "memory"], "default": "cpu"},
        },
    },
    "list_services": {
        "type": "object",
        "properties": {
            "filter_name": {"type": "string"},
            "status": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
        },
    },
    "get_service_status": {
        "type": "object",
        "properties": {"service_name": {"type": "string"}},
        "required": ["service_name"],
    },
    "query_event_log": {
        "type": "object",
        "properties": {
            "log_name": {"type": "string", "default": "System"},
            "count": {"type": "integer"},
            "level": {"type": "string", "enum": ["", "critical", "error", "warning", "info"]},
        },
    },
    "read_registry": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Registry key path"},
            "name": {"type": "string", "description": "Value name (optional)"},
        },
        "required": ["path"],
    },
    "list_installed_programs": {
        "type": "object",
        "properties": {
            "filter_name": {"type": "string"},
            "limit": {"type": "integer"},
        },
    },
    "screenshot": {
        "type": "object",
        "properties": {
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 4,
                "maxItems": 4,
                "description": "Optional [left, top, width, height]",
            }
        },
    },
    "open_app": {
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "App alias (chrome, notepad, edge)"},
            "args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["app"],
    },
    "open_url": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "browser": {"type": "string"},
            "autoplay": {"type": "boolean", "default": True},
        },
        "required": ["url"],
    },
    "focus_window": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "partial": {"type": "boolean", "default": True},
        },
        "required": ["title"],
    },
    "type_text": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "interval": {"type": "number", "default": 0.02},
            "target_field": {"type": "string"},
        },
        "required": ["text"],
    },
    "press_keys": {
        "type": "object",
        "properties": {
            "keys": {"type": "array", "items": {"type": "string"}},
            "presses": {"type": "integer"},
        },
        "required": ["keys"],
    },
    "click": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "clicks": {"type": "integer"},
        },
        "required": ["x", "y"],
    },
    "move_mouse": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "duration": {"type": "number", "default": 0.2},
        },
        "required": ["x", "y"],
    },
    "create_folder": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Folder path or name"},
        },
        "required": ["path"],
    },
    "open_path": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or folder path to open"},
        },
        "required": ["path"],
    },
    "install_program": {
        "type": "object",
        "properties": {
            "package": {"type": "string", "description": "Program or winget package name"},
        },
        "required": ["package"],
    },
    "list_directory": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to list"},
        },
    },
    "write_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Destination file path"},
            "content": {
                "type": "string",
                "description": (
                    "Exact literal text to write into the file — nothing else. "
                    "Do not include user meta instructions (e.g. 'write exactly', 'nothing else')."
                ),
            },
            "append": {"type": "boolean", "default": False},
        },
        "required": ["path", "content"],
    },
    "create_word_document": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    "delete_path": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "recursive": {"type": "boolean", "default": False},
        },
        "required": ["path"],
    },
    "download_file": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "File URL to download"},
            "path": {"type": "string", "description": "Destination path or filename under Downloads"},
        },
        "required": ["url"],
    },
    "git_clone": {
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "Git repository URL"},
            "target_dir": {"type": "string", "description": "Clone destination folder"},
            "branch": {"type": "string", "description": "Optional branch name"},
        },
        "required": ["repo_url"],
    },
    "run_command": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "shell": {"type": "string", "enum": ["powershell", "cmd"], "default": "powershell"},
        },
        "required": ["command"],
    },
}

_LOCAL_TOOL_PATTERN = re.compile("LOCAL_TOOL", re.IGNORECASE)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
            continue
        if char != "}":
            continue
        depth -= 1
        if depth == 0:
            try:
                parsed = json.loads(text[start : index + 1])
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


@dataclass(frozen=True)
class LocalToolRequest:
    name: str
    arguments: dict[str, Any]
    execution_target: str = "client"
    mission_id: str | None = None
    step_id: str | None = None


def input_schema_for_tool(tool_name: str, tool: Any | None = None) -> dict[str, Any]:
    if tool is not None and hasattr(tool, "get_parameters_schema"):
        schema = tool.get_parameters_schema()
        if schema:
            return schema
    return _TOOL_INPUT_SCHEMAS.get(tool_name, {"type": "object", "properties": {}})


def build_tool_manifest(registry: ToolRegistry) -> list[dict[str, Any]]:
    """Full local tools manifest for Hermes Server session/context."""
    manifest: list[dict[str, Any]] = []
    for definition in sorted(registry.list_tools(), key=lambda d: (d.category, d.name)):
        tool = registry.get(definition.name)
        read_only = definition.risk_level == RiskLevel.READ_ONLY
        manifest.append(
            {
                "name": definition.name,
                "category": definition.category,
                "description": definition.description,
                "read_only": read_only,
                "risk_level": definition.risk_level.value,
                "execution_target": definition.execution_target.value,
                "input_schema": input_schema_for_tool(definition.name, tool),
            }
        )
    return manifest


def parse_local_tool_request(text: str) -> LocalToolRequest | None:
    """Parse Hermes LOCAL_TOOL delegation line from assistant output."""
    if not text or not _LOCAL_TOOL_PATTERN.search(text):
        return None
    match = _LOCAL_TOOL_PATTERN.search(text)
    if match is None:
        raise AssertionError
    payload = _extract_json_object(text[match.end() :])
    if not payload:
        return None
    name = payload.get("name") or payload.get("tool")
    if not name or not isinstance(name, str):
        return None
    arguments = payload.get("arguments") or payload.get("args") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    execution_target = str(
        payload.get("execution_target") or payload.get("executionTarget") or "client"
    ).strip().lower()
    return LocalToolRequest(
        name=name.strip(),
        arguments=arguments,
        execution_target=execution_target or "client",
        mission_id=payload.get("mission_id") or payload.get("missionId"),
        step_id=payload.get("step_id") or payload.get("stepId"),
    )


_LOCAL_FILE_EXTENSIONS = frozenset(
    {
        "txt",
        "pdf",
        "doc",
        "docx",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "md",
        "csv",
        "xls",
        "xlsx",
        "zip",
        "rar",
        "7z",
        "exe",
        "dll",
        "bat",
        "ps1",
        "json",
        "xml",
        "html",
        "htm",
        "log",
        "ini",
        "cfg",
        "yaml",
        "yml",
    }
)


def _looks_like_local_filename(value: str) -> bool:
    host = (value or "").split("/")[0].split("://")[-1].split(":")[0].strip().lower()
    if "." not in host:
        return False
    ext = host.rsplit(".", 1)[-1]
    return ext in _LOCAL_FILE_EXTENSIONS


def extract_url_hint(text: str) -> str | None:
    """Extract a URL or domain from user text for multi-step navigation hints."""
    url_match = re.search(r"""https?://[^\s\"']+""", text, re.IGNORECASE)
    if url_match:
        value = url_match.group(0)
        if _looks_like_local_filename(value):
            return None
        return value
    domain_match = re.search(
        r"""(?:https?://)?([a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?:/[^\s\"']*)?)""",
        text,
        re.IGNORECASE,
    )
    if domain_match:
        value = domain_match.group(0)
        if _looks_like_local_filename(value):
            return None
        if not value.startswith("http"):
            return f"https://{value.lstrip('/')}"
        return value
    return None


def format_tool_result_message(
    tool_name: str,
    result: ToolResultPayload,
    user_message: str = "",
) -> str:
    """User/run input sent back to Hermes after local execution."""
    payload = {
        "name": tool_name,
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }
    message = (
        "TOOL_RESULT "
        + json.dumps(payload, ensure_ascii=False, default=str)
        + "\n\nYukaridaki yerel tool sonucunu kullanarak kullaniciya kisa ve anlasilir Turkce ozet ver. Ham JSON tekrarlama."
    )
    url_hint = extract_url_hint(user_message)
    if tool_name == "open_app" and url_hint and result.success:
        message = (
            "TOOL_RESULT "
            + json.dumps(payload, ensure_ascii=False, default=str)
            + "\n\nDevam adimi: Kullanici ayrica "
            + url_hint
            + ' adresine gitmek istedi. Ozeti henuz verme — once LOCAL_TOOL {"name": "open_url", "arguments": {"url": "'
            + url_hint
            + '"}} cagir.'
        )
    elif tool_name == "download_file" and result.success:
        message += (
            "\n\nSONRAKI ADIM: Indirme tamamlandi. Kullanici kurulum istediyse run_command veya install_program "
            "ile devam et. open_url KULLANMA."
        )
    elif tool_name == "create_folder" and result.success:
        message += (
            "\n\nSONRAKI ADIM: Klasor hazir. Indirme veya git clone gerekiyorsa download_file / git_clone ile devam et."
        )
    elif tool_name == "git_clone" and result.success:
        message += "\n\nSONRAKI ADIM: Repo klonlandi. Gerekirse run_command ile bagimlilik kur veya projeyi ac."
    elif tool_name == "install_program" and result.success:
        message += "\n\nSONRAKI ADIM: Kurulum tamamlandi. Kullaniciya kisa Turkce ozet ver."
    elif tool_name == "create_word_document" and result.success:
        message += (
            "\n\nSONRAKI ADIM: Docx olusturuldu. TOOL_RESULT icindeki verified_listing ile dogrula; "
            "kullaniciya dosya yolunu ve boyutunu soyle. open_url veya run_command KULLANMA."
        )
    elif tool_name == "write_file" and result.success:
        message += "\n\nSONRAKI ADIM: Dosya yazildi. verified_listing ile dogrula."
    elif tool_name == "delete_path" and result.success:
        message += (
            "\n\nSONRAKI ADIM: Silme tamamlandi. exists_after=false ve verified_listing ile kanitla."
        )
    elif tool_name == "list_directory" and result.success:
        message += "\n\nSONRAKI ADIM: Listeyi kullanarak dosyanin gercekten var/yok oldugunu acikla."
    return message
