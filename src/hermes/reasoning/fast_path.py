"""Fast path — resolve common local commands without a server LLM round-trip.

The V3 runtime normally asks the server-side LLM what to do on every turn.
For a well‑known class of explicit, high‑confidence requests ("google'u ac",
"chrome'u baslat", "masaustunde klasor olustur", "sesi 50 yap") that is both
slow and fragile: the model can misfire, ask for a URL that is obvious, or
take minutes understanding a trivial task.

``FastPathPlanner`` plugs into ``ReasoningRuntime`` **before** the LLM call.
It runs the battle-tested ``ToolIntentMatcher`` (a Turkish/English rule
matcher) against a ``ConversationalContext`` rebuilt from the World Model
snapshot, then maps the matched V2 tool request onto a V3 capability call.

Rules:

  * Only a confident match (``confidence >= action_threshold``) becomes an
    ``ACTION`` decision. Everything else falls through to the LLM — the fast
    path never *guesses*.
  * A confident *ambiguous* result (missing file/folder path) becomes a fast
    ``USER_QUESTION`` instead of burning an LLM call.
  * The orchestrator only lets the fast path fire on the first reasoning
    iteration of a turn, so it can never re-open the same site forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermes.agent.tool_intent import ToolIntentMatcher
from hermes.context.conversational_context import ConversationalContext
from hermes.reasoning.decision import Decision

# V2 local tool name → (V3 capability, argument keys the capability accepts).
# Argument keys are filtered so payloads never smuggle mission metadata into
# a tool's JSON schema.
_LOCAL_TO_CAPABILITY: dict[str, tuple[str, tuple[str, ...]]] = {
    "open_url": ("browser.navigate", ("url",)),
    "open_app": ("application.open", ("app",)),
    "create_folder": ("filesystem.write", ("path",)),
    "read_file": ("filesystem.read", ("path",)),
    "list_directory": ("filesystem.list", ("path",)),
    "open_path": ("filesystem.open", ("path",)),
    "delete_path": ("filesystem.delete", ("path",)),
    "rename_path": ("filesystem.rename", ("path", "new_name")),
    "copy_file": ("filesystem.copy", ("source", "destination")),
    "move_file": ("filesystem.move", ("source", "destination")),
    "set_volume": ("system.configure", ("action", "level")),
    "run_command": ("terminal.execute", ("command", "shell")),
    "screenshot": ("screen.observe", ()),
}


def _context_from_world(world_model: Any) -> tuple[ConversationalContext, dict[str, str]]:
    """Rebuild a V2 ``ConversationalContext`` from a V3 world snapshot.

    Only facts that are actually present are carried over, so the matcher
    cannot split on invented file paths. Best-effort: a broken snapshot
    degrades to an empty context (explicit-path commands still work).
    """
    ctx = ConversationalContext()
    refs: dict[str, str] = {}
    try:
        snapshot = world_model.snapshot()
    except Exception:
        return ctx, refs
    raw_refs = snapshot.get("references") if isinstance(snapshot, dict) else None
    if not isinstance(raw_refs, dict):
        return ctx, refs
    for key, entry in raw_refs.items():
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if isinstance(value, str) and value.strip():
            refs[key] = value.strip()
    ctx.last_created_file = refs.get("last_created_file")
    ctx.last_opened_file = refs.get("last_opened_file")
    ctx.last_application = refs.get("last_application")
    ctx.last_url = refs.get("last_url")
    ctx.last_browser_url = refs.get("last_browser_url")
    ctx.recent_files = [
        path for path in (ctx.last_created_file, ctx.last_opened_file) if path
    ]
    ctx.recent_folders = [
        path for path in (refs.get("last_folder"), refs.get("last_created_folder")) if path
    ]
    return ctx, refs


@dataclass
class FastPathPlanner:
    """Converts a confident local match into a V3 ``Decision`` or ``None``."""

    tool_registry: Any = None
    action_threshold: float = 0.8
    question_threshold: float = 0.45
    _matcher: ToolIntentMatcher | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self._matcher is None:
            self._matcher = ToolIntentMatcher(self.tool_registry)

    def resolve(self, message: str, world_model: Any) -> Decision | None:
        """Return a fast V3 decision for ``message``, or ``None`` (use LLM)."""
        if not message or not message.strip():
            return None
        ctx, refs = _context_from_world(world_model)
        try:
            result = self._matcher.match(message, ctx, resolved_references=refs)
        except Exception:
            return None
        if result is None:
            return None

        if result.intent is not None and result.confidence >= self.action_threshold:
            request = result.intent.request
            mapping = _LOCAL_TO_CAPABILITY.get(request.name)
            if mapping is None:
                return None
            capability, accepted = mapping
            arguments = {
                key: value
                for key, value in (request.arguments or {}).items()
                if key in accepted and value is not None
            }
            if capability == "system.configure" and "level" in arguments:
                action = str(arguments.get("action") or "")
                level = arguments.get("level")
                if action == "set" and level is not None:
                    arguments = {"action": "set", "level": int(level)}
            return Decision.of_action(
                capability,
                arguments,
                rationale=f"local fast path: {request.name}",
                expected_observation=f"{capability} result",
            )

        if result.ambiguous and result.clarification and result.confidence >= self.question_threshold:
            return Decision.of_user_question(result.clarification, why="local fast path")

        return None


__all__ = ["FastPathPlanner", "_LOCAL_TO_CAPABILITY"]