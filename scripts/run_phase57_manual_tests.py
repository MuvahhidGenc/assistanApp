"""Real Windows client execution checks for PHASE 5.7 manual test list."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from hermes.agent.goal_router import GoalRouter
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.tools.registry import create_default_registry


DESKTOP = Path.home() / "Desktop"
FOLDER_2026 = DESKTOP / "Deneme2026"
FOLDER_DENEME = DESKTOP / "deneme"
TEST_FILE = FOLDER_2026 / "test.txt"


def cleanup() -> None:
    for path in (TEST_FILE, FOLDER_2026, FOLDER_DENEME):
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


async def run_tool(name: str, **arguments):
    registry = create_default_registry()
    tool = registry.get(name)
    if tool is None:
        raise RuntimeError(f"Tool not found: {name}")
    return await tool.execute(**arguments)


def resolve_intent(message: str, ctx: ConversationalContext):
    resolver = ReferenceResolver()
    resolution = resolver.resolve(message, ctx)
    if resolution.intent:
        return resolution.intent, "reference_resolver"
    route = GoalRouter(create_default_registry()).route(message, ctx)
    if route.intent:
        return route.intent, "goal_router"
    return None, "none"


async def main() -> int:
    cleanup()
    ctx = ConversationalContext(
        active_folder=str(DESKTOP / "Hermes"),
        last_created_folder=str(DESKTOP / "Hermes"),
    )
    results: list[tuple[str, bool, str]] = []

    async def check(label: str, ok: bool, detail: str) -> None:
        results.append((label, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {label}: {detail}")

    cases = [
        (
            "1 adli klasor create",
            "Masaustune Deneme2026 adli bir klasor olustur.",
            "create_folder",
            lambda: FOLDER_2026.is_dir(),
            lambda intent: FOLDER_2026,
        ),
        (
            "2 open Deneme2026",
            "Deneme2026 klasorunu ac.",
            "open_path",
            lambda: True,
            None,
        ),
        (
            "3 empty test.txt",
            "icine test.txt olustur",
            "write_file",
            lambda: TEST_FILE.is_file() and TEST_FILE.read_text(encoding="utf-8") == "",
            None,
        ),
        (
            "4 test.txt Merhaba",
            "icine test.txt olustur ve icine Merhaba yaz",
            "write_file",
            lambda: TEST_FILE.read_text(encoding="utf-8") == "Merhaba",
            None,
        ),
        (
            "5 deneme adinda masaustunde",
            "deneme adinda bir klasor olustur masaustunde",
            "create_folder",
            lambda: FOLDER_DENEME.is_dir(),
            lambda intent: FOLDER_DENEME,
        ),
    ]

    for label, message, expected_tool, verify_fn, path_fn in cases:
        intent, source = resolve_intent(message, ctx)
        ok = intent is not None and intent.request.name == expected_tool
        detail = f"source={source}"
        if ok and expected_tool == "create_folder":
            path = path_fn(intent) if path_fn else Path(intent.request.arguments["path"])
            result = await run_tool("create_folder", path=str(path))
            ok = result.success and verify_fn()
            ctx.active_folder = str(path)
            ctx.last_created_folder = str(path)
        elif ok and expected_tool == "write_file":
            args = intent.request.arguments
            result = await run_tool("write_file", **args)
            ok = result.success and verify_fn()
            ctx.active_file = args["path"]
            ctx.last_created_file = args["path"]
        elif ok and expected_tool == "open_path":
            path = intent.request.arguments["path"]
            ok = "Deneme2026" in str(path) and "Hermes" not in str(path)
            if ok:
                result = await run_tool("open_path", path=path)
                ok = result.success
        else:
            detail += f", intent={getattr(intent, 'request', None) and intent.request.name}"
        await check(label, ok, detail)

    failed = [label for label, ok, _ in results if not ok]
    print(f"\nSummary: {len(results) - len(failed)}/{len(results)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
