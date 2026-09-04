"""Central tool catalog — metadata for planning, routing, and verification."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.execution_target import ExecutionTarget
from hermes.tools.registry import ToolRegistry
from hermes.tools.verifiers.registry import create_default_verifier_registry


class IdempotencyKind(StrEnum):
    SAFE_REPEAT = "safe_repeat"
    CREATE_IF_MISSING = "create_if_missing"
    OVERWRITE = "overwrite"
    DESTRUCTIVE = "destructive"
    STATEFUL = "stateful"


@dataclass(frozen=True)
class ToolCatalogEntry:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_hint: str
    side_effects: str
    verification_method: str
    execution_target: ExecutionTarget
    risk_level: RiskLevel
    idempotency: IdempotencyKind
    prerequisites: tuple[str, ...] = ()
    opens_ui: bool = False
    category: str = "general"
    capabilities: tuple[str, ...] = ()
    fallback_tools: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output": self.output_hint,
            "side_effects": self.side_effects,
            "verification_method": self.verification_method,
            "execution_target": self.execution_target.value,
            "risk_level": self.risk_level.value,
            "idempotency": self.idempotency.value,
            "prerequisites": list(self.prerequisites),
            "opens_ui": self.opens_ui,
            "category": self.category,
            "capabilities": list(self.capabilities),
            "fallback_tools": list(self.fallback_tools),
        }


_UI_TOOLS = frozenset(
    {
        "open_app",
        "open_path",
        "open_url",
        "browser_nav",
        "focus_window",
    }
)

_IDEMPOTENCY: dict[str, IdempotencyKind] = {
    "create_folder": IdempotencyKind.CREATE_IF_MISSING,
    "write_file": IdempotencyKind.OVERWRITE,
    "copy_file": IdempotencyKind.SAFE_REPEAT,
    "move_file": IdempotencyKind.STATEFUL,
    "rename_path": IdempotencyKind.STATEFUL,
    "delete_path": IdempotencyKind.DESTRUCTIVE,
    "open_app": IdempotencyKind.SAFE_REPEAT,
    "open_path": IdempotencyKind.SAFE_REPEAT,
    "list_directory": IdempotencyKind.SAFE_REPEAT,
    "search_files": IdempotencyKind.SAFE_REPEAT,
    "read_screen_text": IdempotencyKind.SAFE_REPEAT,
    "screenshot": IdempotencyKind.SAFE_REPEAT,
}

_SIDE_EFFECTS: dict[str, str] = {
    "create_folder": "filesystem: mkdir",
    "write_file": "filesystem: write",
    "copy_file": "filesystem: copy",
    "move_file": "filesystem: move",
    "rename_path": "filesystem: rename",
    "delete_path": "filesystem: delete",
    "open_app": "process: launch or focus window",
    "open_path": "shell: open in Explorer/editor",
    "list_directory": "none (read-only)",
    "search_files": "none (read-only)",
    "run_command": "subprocess execution",
}


class ToolCatalog:
    """Registry-backed catalog with planning metadata."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._verifiers = create_default_verifier_registry()
        self._entries: dict[str, ToolCatalogEntry] = {}
        self._build()

    def _build(self) -> None:
        for definition in self._registry.list_tools():
            name = definition.name
            verifier = self._verifiers.get(name)
            verification = (
                verifier.tool_names[0] + "_verifier"
                if verifier and getattr(verifier, "tool_names", None)
                else "output_present"
            )
            self._entries[name] = ToolCatalogEntry(
                name=name,
                description=definition.description,
                input_schema=definition.parameters_schema,
                output_hint=f"{name} result payload",
                side_effects=_SIDE_EFFECTS.get(name, "local execution"),
                verification_method=verification,
                execution_target=definition.execution_target,
                risk_level=definition.risk_level,
                idempotency=_IDEMPOTENCY.get(name, IdempotencyKind.STATEFUL),
                prerequisites=definition.prerequisites,
                opens_ui=name in _UI_TOOLS,
                category=definition.category,
                capabilities=definition.capabilities,
                fallback_tools=definition.fallback_tools,
            )

    def get(self, name: str) -> ToolCatalogEntry | None:
        return self._entries.get(name)

    def find_by_capability(self, capability: str) -> list[ToolCatalogEntry]:
        wanted = str(capability)
        return [entry for entry in self._entries.values() if wanted in entry.capabilities]

    def capabilities(self) -> list[str]:
        found: set[str] = set()
        for entry in self._entries.values():
            found.update(entry.capabilities)
        return sorted(found)

    def list_entries(self) -> list[ToolCatalogEntry]:
        return list(self._entries.values())

    def to_manifest(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.list_entries()]

    def background_tools(self) -> frozenset[str]:
        return frozenset(name for name, entry in self._entries.items() if not entry.opens_ui)

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def __len__(self) -> int:
        return len(self._entries)


def build_tool_catalog(registry: ToolRegistry | None = None) -> ToolCatalog:
    from hermes.tools.registry import create_default_registry

    return ToolCatalog(registry or create_default_registry())
