"""What tools can *do*, independent of what they are called.

The agent asks for a capability (``filesystem.search``) rather than a tool name
(``search_files``). This is what lets a new goal reach existing tools without a
new keyword branch, and what gives recovery a principled list of alternatives.

Capabilities are plain strings. `Capability` names the ones in use today; the
index accepts any string so a new tool can introduce a new capability without
editing this enum.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hermes.config.settings import RiskLevel

if TYPE_CHECKING:
    from hermes.tools.registry import ToolRegistry


class Capability(StrEnum):
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    FILESYSTEM_LIST = "filesystem.list"
    FILESYSTEM_SEARCH = "filesystem.search"
    FILESYSTEM_COPY = "filesystem.copy"
    FILESYSTEM_MOVE = "filesystem.move"
    FILESYSTEM_RENAME = "filesystem.rename"
    FILESYSTEM_DELETE = "filesystem.delete"
    FILESYSTEM_OPEN = "filesystem.open"
    FILESYSTEM_INSPECT = "filesystem.inspect"

    BROWSER_NAVIGATE = "browser.navigate"
    BROWSER_BACK = "browser.back"
    BROWSER_READ = "browser.read"
    BROWSER_CLICK = "browser.click"
    BROWSER_OPEN = "browser.open"

    DOCUMENT_READ = "document.read"
    DOCUMENT_CREATE = "document.create"
    DOCUMENT_WRITE = "document.write"

    APPLICATION_OPEN = "application.open"
    APPLICATION_INSTALL = "application.install"
    APPLICATION_INSPECT = "application.inspect"

    SCREEN_OBSERVE = "screen.observe"
    SCREEN_READ = "screen.read"
    SCREEN_RESOLVE = "screen.resolve"
    SCREEN_CLICK = "screen.click"
    SCREEN_TYPE = "screen.type"
    SCREEN_SCROLL = "screen.scroll"

    KEYBOARD_INPUT = "keyboard.input"
    MOUSE_CONTROL = "mouse.control"

    WINDOW_INSPECT = "window.inspect"
    WINDOW_FOCUS = "window.focus"
    WINDOW_MANAGE = "window.manage"

    CLIPBOARD_READ = "clipboard.read"
    CLIPBOARD_WRITE = "clipboard.write"

    PROCESS_INSPECT = "process.inspect"
    PROCESS_MANAGE = "process.manage"
    SERVICE_INSPECT = "service.inspect"
    SERVICE_MANAGE = "service.manage"

    NETWORK_INSPECT = "network.inspect"
    NETWORK_CONFIGURE = "network.configure"
    WEB_DOWNLOAD = "web.download"

    SYSTEM_INSPECT = "system.inspect"
    SYSTEM_CONFIGURE = "system.configure"
    SYSTEM_DIAGNOSE = "system.diagnose"

    TERMINAL_EXECUTE = "terminal.execute"
    CODE_CLONE = "code.clone"

    DEBUG_ECHO = "debug.echo"


# Capabilities of the tools registered today. A tool class may instead declare
# `capabilities` on itself; that declaration wins over this table.
_TOOL_CAPABILITIES: dict[str, tuple[Capability, ...]] = {
    # computer_control
    "screenshot": (Capability.SCREEN_OBSERVE,),
    "open_app": (Capability.APPLICATION_OPEN, Capability.BROWSER_OPEN),
    "open_url": (Capability.BROWSER_NAVIGATE, Capability.APPLICATION_OPEN, Capability.BROWSER_OPEN),
    "focus_window": (Capability.WINDOW_FOCUS,),
    "type_text": (Capability.KEYBOARD_INPUT, Capability.SCREEN_TYPE),
    "press_keys": (Capability.KEYBOARD_INPUT,),
    "click": (Capability.MOUSE_CONTROL, Capability.SCREEN_CLICK),
    "move_mouse": (Capability.MOUSE_CONTROL,),
    "scroll": (Capability.SCREEN_SCROLL,),
    "click_text": (Capability.SCREEN_CLICK, Capability.BROWSER_CLICK),
    "resolve_screen_entity": (Capability.SCREEN_RESOLVE,),
    "show_desktop": (Capability.WINDOW_MANAGE,),
    "browser_nav": (Capability.BROWSER_NAVIGATE, Capability.BROWSER_BACK),
    # pc_actions
    "set_dns": (Capability.NETWORK_CONFIGURE,),
    "list_windows": (Capability.WINDOW_INSPECT,),
    "set_volume": (Capability.SYSTEM_CONFIGURE,),
    "get_clipboard": (Capability.CLIPBOARD_READ,),
    "set_clipboard": (Capability.CLIPBOARD_WRITE,),
    "kill_process": (Capability.PROCESS_MANAGE,),
    "control_service": (Capability.SERVICE_MANAGE,),
    "read_screen_text": (
        Capability.SCREEN_READ,
        Capability.SCREEN_OBSERVE,
        Capability.BROWSER_READ,
    ),
    "create_folder": (Capability.FILESYSTEM_WRITE,),
    "open_path": (Capability.FILESYSTEM_OPEN, Capability.APPLICATION_OPEN),
    "install_program": (Capability.APPLICATION_INSTALL,),
    "download_file": (Capability.WEB_DOWNLOAD,),
    "git_clone": (Capability.CODE_CLONE,),
    "run_command": (Capability.TERMINAL_EXECUTE,),
    # file_tools
    "read_file": (Capability.FILESYSTEM_READ, Capability.DOCUMENT_READ),
    "list_directory": (Capability.FILESYSTEM_LIST,),
    "write_file": (
        Capability.FILESYSTEM_WRITE,
        Capability.DOCUMENT_CREATE,
        Capability.DOCUMENT_WRITE,
    ),
    "create_word_document": (Capability.DOCUMENT_CREATE, Capability.DOCUMENT_WRITE),
    "copy_file": (Capability.FILESYSTEM_COPY,),
    "move_file": (Capability.FILESYSTEM_MOVE,),
    "rename_path": (Capability.FILESYSTEM_RENAME,),
    "search_files": (Capability.FILESYSTEM_SEARCH,),
    "delete_path": (Capability.FILESYSTEM_DELETE,),
    # network_tools
    "get_network_config": (Capability.NETWORK_INSPECT,),
    "ping_host": (Capability.NETWORK_INSPECT,),
    "dns_lookup": (Capability.NETWORK_INSPECT,),
    "traceroute": (Capability.NETWORK_INSPECT,),
    "check_port": (Capability.NETWORK_INSPECT,),
    # process_tools
    "list_processes": (Capability.PROCESS_INSPECT,),
    "list_services": (Capability.SERVICE_INSPECT,),
    "get_service_status": (Capability.SERVICE_INSPECT,),
    # system_tools
    "get_system_info": (Capability.SYSTEM_INSPECT,),
    "get_disk_info": (Capability.SYSTEM_INSPECT, Capability.FILESYSTEM_INSPECT),
    "list_installed_programs": (Capability.APPLICATION_INSPECT,),
    # diagnostic_tools
    "query_event_log": (Capability.SYSTEM_DIAGNOSE,),
    "read_registry": (Capability.SYSTEM_INSPECT,),
    # debug_tools
    "echo": (Capability.DEBUG_ECHO,),
}

# Alternative tools to try when the primary one fails. Ordered best-first, and
# deliberately conservative: every entry must be a genuinely different mechanism
# rather than a retry of the same thing.
_TOOL_FALLBACKS: dict[str, tuple[str, ...]] = {
    "read_screen_text": ("screenshot",),
    "click_text": ("click",),
    "open_url": ("browser_nav", "open_app"),
    "open_app": ("open_path", "run_command"),
    "open_path": ("open_app",),
    "install_program": ("download_file", "run_command"),
    "move_file": ("copy_file",),
    "git_clone": ("run_command",),
    "set_dns": ("run_command",),
    "list_installed_programs": ("run_command",),
}

# Conditions that must hold before a tool can succeed. Consumed by planning and
# recovery to explain failures instead of blindly retrying.
_TOOL_PREREQUISITES: dict[str, tuple[str, ...]] = {
    "read_screen_text": ("target window must be open and focusable",),
    "click_text": ("target text must be visible on screen",),
    "browser_nav": ("a browser window must be open",),
    "write_file": ("parent directory must exist",),
    "copy_file": ("source path must exist",),
    "move_file": ("source path must exist",),
    "rename_path": ("source path must exist",),
    "delete_path": ("target path must exist",),
    "read_file": ("target file must exist",),
    "git_clone": ("git must be installed",),
    "install_program": ("package manager must be available",),
}


def capabilities_for(tool_name: str, declared: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Capabilities of a tool; a self-declaration wins over the table."""
    if declared:
        return tuple(str(item) for item in declared)
    return tuple(str(item) for item in _TOOL_CAPABILITIES.get(tool_name, ()))


def fallbacks_for(tool_name: str, declared: tuple[str, ...] = ()) -> tuple[str, ...]:
    if declared:
        return tuple(declared)
    return _TOOL_FALLBACKS.get(tool_name, ())


def prerequisites_for(tool_name: str, declared: tuple[str, ...] = ()) -> tuple[str, ...]:
    if declared:
        return tuple(declared)
    return _TOOL_PREREQUISITES.get(tool_name, ())


def build_capability_index(
    tool_capabilities: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    """Invert tool→capabilities into capability→tools."""
    index: dict[str, list[str]] = defaultdict(list)
    for tool_name, caps in tool_capabilities.items():
        for capability in caps:
            index[str(capability)].append(tool_name)
    return {capability: tuple(names) for capability, names in index.items()}


def known_capabilities() -> tuple[str, ...]:
    return tuple(sorted({str(item) for item in Capability}))


def capability_risk_floor(
    registry: ToolRegistry, capability: str
) -> RiskLevel | None:
    """Lowest risk any tool can achieve for this capability.

    Derived from the registry rather than a hand-kept list of "dangerous"
    capabilities, so a capability becomes mutating exactly when every tool that
    provides it mutates something. None means nothing provides it.
    """
    levels = [
        definition.risk_level
        for definition in registry.find_by_capability(capability)
        if definition.risk_level is not None
    ]
    if not levels:
        return None
    return min(levels, key=lambda level: level.severity)


@dataclass(frozen=True)
class CapabilitySelection:
    """A concrete tool chosen to satisfy a capability, with fitted arguments."""

    capability: str
    tool_name: str
    arguments: dict[str, Any]


def fit_arguments(
    arguments: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any] | None:
    """Keep only the arguments a tool accepts; None if a required one is missing.

    This is what lets one caller hand the same payload to different tools
    without per-pair mapping code, and refuse the ones it cannot actually call.
    """
    properties = schema.get("properties") or {}
    if not properties:
        return {}
    fitted = {key: value for key, value in arguments.items() if key in properties}
    for required in schema.get("required") or []:
        value = fitted.get(required)
        if value is None or value == "":
            return None
    return fitted


def select_tool_for_capability(
    registry: ToolRegistry,
    capability: str,
    inputs: dict[str, Any] | None = None,
    *,
    exclude: tuple[str, ...] = (),
) -> CapabilitySelection | None:
    """Best tool providing the capability that can be called with `inputs`.

    Candidates arrive lowest-risk first. Among those, the one that consumes the
    most of the supplied inputs wins: a tool ignoring half the payload is
    answering a narrower question than the caller asked (create_folder vs
    write_file when both path and content were given).
    """
    payload = dict(inputs or {})
    if str(capability) == "browser.back":
        payload.setdefault("action", "back")
    path = str(payload.get("path") or "")
    best: CapabilitySelection | None = None
    best_coverage = -1
    best_arity = 10**9
    best_cap_count = -1

    for definition in registry.find_by_capability(capability):
        if definition.name in exclude:
            continue
        tool = registry.get(definition.name)
        if tool is None:
            continue
        schema = tool.get_parameters_schema()
        fitted = fit_arguments(payload, schema)
        if fitted is None:
            continue
        # A path with a filename is a file write, not a folder create, even
        # when content has not been supplied yet. create_folder also offers
        # filesystem.write and would otherwise win on a lone `path`.
        looks_like_file = "." in Path(path).name and not Path(path).name.startswith(".")
        if (
            str(capability) in {"document.create", "document.write"}
            and path.casefold().endswith(".docx")
            and definition.name == "write_file"
        ):
            continue
        if (
            str(capability) in {"document.create", "document.write"}
            and path.casefold().endswith(".pdf")
            and definition.name == "create_word_document"
        ):
            continue
        properties = schema.get("properties") or {}
        if (
            str(capability) == "filesystem.write"
            and looks_like_file
            and "path" in properties
            and "content" not in properties
        ):
            continue
        coverage = len(fitted)
        # A tool offered arguments that fit none of them is answering a
        # different question: browser_nav (back/forward) would otherwise
        # satisfy "navigate to this address" by ignoring the address. Being
        # called with nothing is still fine, which is how an observation like
        # "list the windows" works.
        if coverage == 0 and payload and (schema.get("properties") or {}):
            continue
        arity = len(properties)
        cap_count = len(definition.capabilities)
        if coverage > best_coverage or (
            coverage == best_coverage
            and (
                arity < best_arity
                or (arity == best_arity and cap_count > best_cap_count)
            )
        ):
            best_coverage = coverage
            best_arity = arity
            best_cap_count = cap_count
            best = CapabilitySelection(
                capability=str(capability), tool_name=definition.name, arguments=fitted
            )
    return best


# Structured fields a successful capability execution can expose. Used to bind
# a later step's required input without naming tools or writing per-pair ifs.
CAPABILITY_OUTPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "filesystem.search": ("path",),
    "filesystem.list": ("path",),
    "filesystem.read": ("path", "content"),
    "filesystem.write": ("path",),
    "filesystem.copy": ("path", "destination"),
    "filesystem.move": ("path",),
    "filesystem.rename": ("path",),
    "filesystem.open": ("path",),
    "filesystem.inspect": ("path",),
    "document.create": ("path",),
    "document.write": ("path",),
    "document.read": ("path", "content"),
    "browser.navigate": ("url",),
    "browser.open": ("url",),
    "application.open": ("app", "path"),
    "screen.observe": ("screen_state", "entities", "text"),
    "screen.read": ("text", "title"),
    "screen.resolve": ("x", "y", "entity_id", "text", "bbox", "state_id"),
    "web.download": ("path",),
    "code.clone": ("path",),
}


def extract_structured_value(output: Any, field: str) -> Any:
    """Pull a conventional field out of a tool/verifier payload.

    Understands nested search matches and verified_output wrappers. Does not
    invent a value when the field is absent.
    """
    if not field:
        return None
    if isinstance(output, list) and field == "path" and output:
        first = output[0]
        if isinstance(first, dict):
            return extract_structured_value(first, "path")
        text = str(first).strip()
        return text or None
    if not isinstance(output, dict):
        return None

    nested = output.get("verified_output")
    if isinstance(nested, dict):
        nested_value = extract_structured_value(nested, field)
        if nested_value not in (None, "", []):
            return nested_value

    direct = output.get(field)
    if isinstance(direct, list) and direct:
        return extract_structured_value(direct, field)
    if direct not in (None, "", []):
        return direct

    if field == "path":
        for key in ("matched_files", "matches", "files"):
            if key not in output:
                continue
            items = output.get(key)
            if isinstance(items, list) and items:
                return extract_structured_value(items, "path")
            # Search ran and produced no files. Do not bind the folder instead.
            return None
        for key in ("destination", "source"):
            value = output.get(key)
            if value not in (None, ""):
                return value
    if field == "url":
        for key in ("opened_url", "last_url", "last_browser_url"):
            value = output.get(key)
            if value not in (None, ""):
                return value
    return None


def input_names_for_capability(registry: ToolRegistry, capability: str) -> set[str]:
    names: set[str] = set()
    for definition in registry.find_by_capability(capability):
        tool = registry.get(definition.name)
        if tool is None:
            continue
        schema = tool.get_parameters_schema()
        names.update(str(item) for item in (schema.get("required") or []))
        names.update(str(key) for key in (schema.get("properties") or {}))
    return names


def produced_fields(capability: str) -> tuple[str, ...]:
    return CAPABILITY_OUTPUT_FIELDS.get(str(capability), ())


def bindable_fields(
    producer_capability: str,
    consumer_capability: str,
    registry: ToolRegistry,
) -> tuple[str, ...]:
    """Every conventional field the producer exposes that the consumer accepts."""
    accepted = input_names_for_capability(registry, consumer_capability)
    return tuple(
        field for field in produced_fields(producer_capability) if field in accepted
    )


def bindable_field(
    producer_capability: str,
    consumer_capability: str,
    registry: ToolRegistry,
) -> str | None:
    """A conventional field the producer exposes that the consumer accepts."""
    fields = bindable_fields(producer_capability, consumer_capability, registry)
    return fields[0] if fields else None


def select_tool_accepting(
    registry: ToolRegistry,
    capability: str,
    field: str,
) -> str | None:
    """Lowest-risk tool for `capability` that declares `field` as an input."""
    for definition in registry.find_by_capability(capability):
        tool = registry.get(definition.name)
        if tool is None:
            continue
        properties = (tool.get_parameters_schema().get("properties") or {})
        if field in properties:
            return definition.name
    return None
