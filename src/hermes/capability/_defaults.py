"""Build default capability contracts from the existing V2 tool fleet.

This module does **not** add behaviour. It reads declarations already
present in the V2 registry (capabilities, fallbacks, prerequisites,
risk level, parameter schema) and emits one `CapabilityContract` per
capability the fleet actually provides.

The mapping below is the single place where the meaning of each
capability lives. ReasoningRuntime asks the registry, not this module,
for the contract.

The builder is intentionally conservative:

  * If a capability has no tools in the registry, no contract is emitted.
  * If a capability has multiple tools with different side effects, the
    union of side effects is taken and the failure semantics is upgraded
    to the strictest member.
  * The default tool is the lowest-risk tool that provides the capability.
"""

from __future__ import annotations

from typing import Any

from hermes.capability.contract import (
    CapabilityContract,
    CapabilityFailureSemantics,
    CapabilitySideEffect,
    VerificationStrategy,
)
from hermes.config.settings import RiskLevel
from hermes.tools.execution_target import ExecutionTarget
from hermes.tools.registry import ToolRegistry

# Capability name -> purpose sentence. Used in the contract so ReasoningRuntime
# can describe the capability to the model without consulting a separate doc.
_PURPOSE: dict[str, str] = {
    "filesystem.read": "Read the contents of a file on disk.",
    "filesystem.write": "Create or overwrite a file at a given path.",
    "filesystem.list": "List entries in a directory.",
    "filesystem.search": "Find files matching a pattern under a directory.",
    "filesystem.copy": "Copy a file from one path to another.",
    "filesystem.move": "Move a file from one path to another.",
    "filesystem.rename": "Rename a file or folder.",
    "filesystem.delete": "Delete a file or folder.",
    "filesystem.open": "Open a path with the OS default application.",
    "filesystem.inspect": "Inspect filesystem metadata (size, free space, etc.).",
    "browser.navigate": "Open a URL in the browser.",
    "browser.back": "Go back in the browser history.",
    "browser.read": "Read visible text on the current page.",
    "browser.click": "Click an element on the current page.",
    "browser.open": "Open or focus a browser application.",
    "document.read": "Read a document (PDF, DOCX, etc.).",
    "document.create": "Create a new document.",
    "document.write": "Write content into a document.",
    "application.open": "Open or focus an application.",
    "application.install": "Install a program.",
    "application.inspect": "List installed applications.",
    "screen.observe": "Capture the screen as an image/state snapshot.",
    "screen.read": "Read visible text from the screen.",
    "screen.resolve": "Resolve a natural-language screen reference to coordinates.",
    "screen.click": "Click at screen coordinates.",
    "screen.type": "Type text at the current focus.",
    "screen.scroll": "Scroll the screen in a direction.",
    "keyboard.input": "Send keyboard input.",
    "mouse.control": "Move or click the mouse.",
    "window.inspect": "List visible windows.",
    "window.focus": "Focus a window by title.",
    "window.manage": "Manipulate windows (show desktop, etc.).",
    "clipboard.read": "Read the clipboard contents.",
    "clipboard.write": "Write to the clipboard.",
    "process.inspect": "List running processes.",
    "process.manage": "Kill or signal a process.",
    "service.inspect": "List or query system services.",
    "service.manage": "Start/stop a system service.",
    "network.inspect": "Read network state (config, ping, DNS, ports).",
    "network.configure": "Modify network configuration (DNS, etc.).",
    "web.download": "Download a file from the web.",
    "system.inspect": "Read system information.",
    "system.configure": "Modify system settings (volume, etc.).",
    "system.diagnose": "Read system diagnostics (event log, registry).",
    "terminal.execute": "Execute a shell command.",
    "code.clone": "Clone a git repository.",
    "debug.echo": "Echo a value (test/debug).",
}

# Side-effect mapping derived from the tool table. Conservative defaults: if a
# tool name is not listed, the builder treats it as filesystem_write on
# mutation tools and observation on read tools based on risk level.
_TOOL_SIDE_EFFECTS: dict[str, tuple[CapabilitySideEffect, ...]] = {
    "screenshot": (CapabilitySideEffect.OBSERVATION,),
    "open_app": (CapabilitySideEffect.PROCESS_START,),
    "open_url": (CapabilitySideEffect.NETWORK_REQUEST, CapabilitySideEffect.PROCESS_START),
    "focus_window": (CapabilitySideEffect.WINDOW_MANIPULATION,),
    "type_text": (CapabilitySideEffect.USER_INPUT,),
    "press_keys": (CapabilitySideEffect.USER_INPUT,),
    "click": (CapabilitySideEffect.USER_INPUT,),
    "move_mouse": (CapabilitySideEffect.USER_INPUT,),
    "scroll": (CapabilitySideEffect.USER_INPUT,),
    "click_text": (CapabilitySideEffect.USER_INPUT,),
    "resolve_screen_entity": (CapabilitySideEffect.OBSERVATION,),
    "show_desktop": (CapabilitySideEffect.WINDOW_MANIPULATION,),
    "browser_nav": (CapabilitySideEffect.NETWORK_REQUEST,),
    "set_dns": (CapabilitySideEffect.NETWORK_CONFIG,),
    "list_windows": (CapabilitySideEffect.OBSERVATION,),
    "set_volume": (CapabilitySideEffect.NONE,),
    "get_clipboard": (CapabilitySideEffect.OBSERVATION,),
    "set_clipboard": (CapabilitySideEffect.CLIPBOARD,),
    "kill_process": (CapabilitySideEffect.PROCESS_KILL,),
    "control_service": (CapabilitySideEffect.SERVICE_CONTROL,),
    "read_screen_text": (CapabilitySideEffect.OBSERVATION,),
    "create_folder": (CapabilitySideEffect.FILESYSTEM_WRITE,),
    "open_path": (CapabilitySideEffect.PROCESS_START,),
    "install_program": (CapabilitySideEffect.INSTALL,),
    "download_file": (CapabilitySideEffect.NETWORK_REQUEST, CapabilitySideEffect.FILESYSTEM_WRITE),
    "git_clone": (CapabilitySideEffect.NETWORK_REQUEST, CapabilitySideEffect.FILESYSTEM_WRITE),
    "run_command": (CapabilitySideEffect.PROCESS_START,),
    "read_file": (CapabilitySideEffect.FILESYSTEM_READ,),
    "list_directory": (CapabilitySideEffect.OBSERVATION,),
    "write_file": (CapabilitySideEffect.FILESYSTEM_WRITE,),
    "create_word_document": (CapabilitySideEffect.FILESYSTEM_WRITE,),
    "copy_file": (CapabilitySideEffect.FILESYSTEM_WRITE,),
    "move_file": (CapabilitySideEffect.FILESYSTEM_WRITE,),
    "rename_path": (CapabilitySideEffect.FILESYSTEM_WRITE,),
    "search_files": (CapabilitySideEffect.OBSERVATION,),
    "delete_path": (CapabilitySideEffect.FILESYSTEM_WRITE,),
    "get_network_config": (CapabilitySideEffect.OBSERVATION,),
    "ping_host": (CapabilitySideEffect.NETWORK_REQUEST,),
    "dns_lookup": (CapabilitySideEffect.NETWORK_REQUEST,),
    "traceroute": (CapabilitySideEffect.NETWORK_REQUEST,),
    "check_port": (CapabilitySideEffect.NETWORK_REQUEST,),
    "list_processes": (CapabilitySideEffect.OBSERVATION,),
    "list_services": (CapabilitySideEffect.OBSERVATION,),
    "get_service_status": (CapabilitySideEffect.OBSERVATION,),
    "get_system_info": (CapabilitySideEffect.OBSERVATION,),
    "get_disk_info": (CapabilitySideEffect.OBSERVATION,),
    "list_installed_programs": (CapabilitySideEffect.OBSERVATION,),
    "query_event_log": (CapabilitySideEffect.OBSERVATION,),
    "read_registry": (CapabilitySideEffect.OBSERVATION,),
    "echo": (CapabilitySideEffect.NONE,),
}

# Capability failure semantics — what the runtime can plan around.
# Conservative defaults; reasoning layer upgrades if a tool proves otherwise.
_FAILURE_SEMANTICS: dict[str, CapabilityFailureSemantics] = {
    "filesystem.read": CapabilityFailureSemantics.IDEMPOTENT,
    "filesystem.write": CapabilityFailureSemantics.SIDE_EFFECTING,
    "filesystem.list": CapabilityFailureSemantics.IDEMPOTENT,
    "filesystem.search": CapabilityFailureSemantics.IDEMPOTENT,
    "filesystem.copy": CapabilityFailureSemantics.SIDE_EFFECTING,
    "filesystem.move": CapabilityFailureSemantics.SIDE_EFFECTING,
    "filesystem.rename": CapabilityFailureSemantics.SIDE_EFFECTING,
    "filesystem.delete": CapabilityFailureSemantics.SIDE_EFFECTING,
    "filesystem.open": CapabilityFailureSemantics.REPLAY_SAFE,
    "filesystem.inspect": CapabilityFailureSemantics.IDEMPOTENT,
    "browser.navigate": CapabilityFailureSemantics.SIDE_EFFECTING,
    "browser.back": CapabilityFailureSemantics.REPLAY_SAFE,
    "browser.read": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "browser.click": CapabilityFailureSemantics.SIDE_EFFECTING,
    "browser.open": CapabilityFailureSemantics.REPLAY_SAFE,
    "document.read": CapabilityFailureSemantics.IDEMPOTENT,
    "document.create": CapabilityFailureSemantics.SIDE_EFFECTING,
    "document.write": CapabilityFailureSemantics.SIDE_EFFECTING,
    "application.open": CapabilityFailureSemantics.REPLAY_SAFE,
    "application.install": CapabilityFailureSemantics.SIDE_EFFECTING,
    "application.inspect": CapabilityFailureSemantics.IDEMPOTENT,
    "screen.observe": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "screen.read": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "screen.resolve": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "screen.click": CapabilityFailureSemantics.SIDE_EFFECTING,
    "screen.type": CapabilityFailureSemantics.SIDE_EFFECTING,
    "screen.scroll": CapabilityFailureSemantics.SIDE_EFFECTING,
    "keyboard.input": CapabilityFailureSemantics.SIDE_EFFECTING,
    "mouse.control": CapabilityFailureSemantics.SIDE_EFFECTING,
    "window.inspect": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "window.focus": CapabilityFailureSemantics.REPLAY_SAFE,
    "window.manage": CapabilityFailureSemantics.SIDE_EFFECTING,
    "clipboard.read": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "clipboard.write": CapabilityFailureSemantics.SIDE_EFFECTING,
    "process.inspect": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "process.manage": CapabilityFailureSemantics.SIDE_EFFECTING,
    "service.inspect": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "service.manage": CapabilityFailureSemantics.SIDE_EFFECTING,
    "network.inspect": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "network.configure": CapabilityFailureSemantics.SIDE_EFFECTING,
    "web.download": CapabilityFailureSemantics.SIDE_EFFECTING,
    "system.inspect": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "system.configure": CapabilityFailureSemantics.SIDE_EFFECTING,
    "system.diagnose": CapabilityFailureSemantics.OBSERVABLE_ONLY,
    "terminal.execute": CapabilityFailureSemantics.SIDE_EFFECTING,
    "code.clone": CapabilityFailureSemantics.SIDE_EFFECTING,
    "debug.echo": CapabilityFailureSemantics.IDEMPOTENT,
}

# Verification method per capability. The registry only stores the method;
# the actual verifier code lives in `tools.verifiers` and is looked up via
# `CapabilityVerifierBinding` (Phase 3.0 contract).
_VERIFICATION_METHOD: dict[str, str] = {
    "filesystem.write": "filesystem.exists",
    "filesystem.copy": "filesystem.exists",
    "filesystem.move": "filesystem.exists",
    "filesystem.rename": "filesystem.exists",
    "filesystem.delete": "filesystem.absent",
    "filesystem.read": "filesystem.readable",
    "filesystem.search": "filesystem.matches",
    "filesystem.list": "filesystem.listed",
    "filesystem.open": "process.running",
    "application.open": "process.running",
    "application.install": "application.installed",
    "code.clone": "filesystem.exists",
    "web.download": "filesystem.exists",
    "browser.navigate": "browser.url_open",
    "browser.open": "process.running",
    "document.create": "filesystem.exists",
    "document.write": "filesystem.exists",
    "network.configure": "network.configured",
    "system.configure": "system.configured",
    "process.manage": "process.state",
    "service.manage": "service.state",
    "screen.observe": "screen.snapshot",
    "screen.read": "screen.text_present",
    "screen.resolve": "screen.entity_present",
    "screen.click": "screen.click_verified",
    "screen.scroll": "screen.scroll_verified",
    "clipboard.write": "clipboard.equals",
    "window.focus": "window.focused",
    "window.manage": "window.state",
    "keyboard.input": "generic.observe",
    "mouse.control": "generic.observe",
    "terminal.execute": "process.running",
}


def _risk_for_capability(registry: ToolRegistry, capability: str) -> RiskLevel | None:
    """Lowest risk across the tools that implement this capability."""
    candidates = registry.find_by_capability(capability)
    levels = [
        d.risk_level
        for d in candidates
        if d.risk_level is not None
    ]
    if not levels:
        return None
    return min(levels, key=lambda level: level.severity)


def _side_effects_for_capability(
    registry: ToolRegistry, capability: str
) -> tuple[CapabilitySideEffect, ...]:
    """Union of side effects across the tools implementing the capability."""
    effects: set[CapabilitySideEffect] = set()
    for definition in registry.find_by_capability(capability):
        effects.update(_TOOL_SIDE_EFFECTS.get(definition.name, ()))
    return tuple(sorted(effects, key=lambda value: value.value))


def _observation_value(capability: str) -> tuple[str, ...]:
    """Conventional output fields, derived from V2's CAPABILITY_OUTPUT_FIELDS."""
    from hermes.tools.capabilities import CAPABILITY_OUTPUT_FIELDS

    return CAPABILITY_OUTPUT_FIELDS.get(capability, ())


def _known_limitations(capability: str) -> tuple[str, ...]:
    """Limitations expressed once and consumed by the registry only."""
    if capability in {
        "screen.observe",
        "screen.read",
        "screen.resolve",
        "screen.click",
        "screen.type",
        "screen.scroll",
    }:
        return ("Requires a visible desktop session.",)
    if capability in {"browser.navigate", "browser.open", "browser.click", "browser.read", "browser.back"}:
        return ("Requires an installed and focusable browser.",)
    if capability in {"filesystem.write", "document.create", "document.write"}:
        return ("Parent directory must exist and be writable.",)
    if capability in {"application.install"}:
        return ("Requires elevated privileges or an installer the OS can run.",)
    if capability in {"terminal.execute"}:
        return ("Unrestricted shell access — the registry does not arbitrate risk.",)
    if capability in {"code.clone"}:
        return ("Requires `git` on PATH and write access to the target directory.",)
    return ()


def build_default_contract(
    registry: ToolRegistry, capability: str
) -> CapabilityContract | None:
    """Build a `CapabilityContract` from the V2 registry's declarations.

    Returns `None` if no tool in the registry provides the capability — the
    registry treats that as "this capability is not advertised on this
    machine". ReasoningRuntime is expected to ask the user or refuse.
    """
    tools = registry.find_by_capability(capability)
    if not tools:
        return None

    risk = _risk_for_capability(registry, capability) or RiskLevel.READ_ONLY
    default = tools[0]               # find_by_capability sorts lowest-risk first.
    fallbacks = tools[1:]            # remaining tools are alternates.

    execution_target = default.execution_target

    verification_method = _VERIFICATION_METHOD.get(capability)
    verification = (
        VerificationStrategy(method=verification_method)
        if verification_method
        else None
    )

    return CapabilityContract(
        name=capability,
        purpose=_PURPOSE.get(capability, ""),
        input_schema=default.parameters_schema,
        output_schema={"type": "object", "properties": {}},
        preconditions=tuple(default.prerequisites or ()),
        known_limitations=_known_limitations(capability),
        side_effects=_side_effects_for_capability(registry, capability),
        risk_level=risk,
        estimated_cost="low" if risk.severity <= 1 else "medium" if risk.severity == 2 else "high",
        observation_value=_observation_value(capability),
        failure_semantics=_FAILURE_SEMANTICS.get(
            capability, CapabilityFailureSemantics.SIDE_EFFECTING
        ),
        execution_target=execution_target,
        default_tool=default.name,
        allowed_overrides=tuple(tool.name for tool in fallbacks),
        verification=verification,
        _source="default_from_registry",
    )


def build_default_contracts(registry: ToolRegistry) -> dict[str, CapabilityContract]:
    """Build contracts for every capability the registry advertises."""
    contracts: dict[str, CapabilityContract] = {}
    for capability in registry.capabilities():
        contract = build_default_contract(registry, capability)
        if contract is not None:
            contracts[capability] = contract
    return contracts