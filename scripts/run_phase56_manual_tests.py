"""Real Windows client execution checks for PHASE 5.6 manual test list."""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from hermes.agent.goal_router import GoalRouter
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.tools.registry import create_default_registry


DESKTOP = Path.home() / "Desktop"
FOLDER_2026 = DESKTOP / "Deneme2026"
FOLDER_2027 = DESKTOP / "Deneme2027"
TEST_FILE = FOLDER_2026 / "test.txt"


def cleanup() -> None:
    for path in (TEST_FILE, FOLDER_2026, FOLDER_2027):
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

    # 1. Create Deneme2026
    msg1 = "Masaustune yeni bir klasor olustur, adina Deneme2026 koy."
    intent1, source1 = resolve_intent(msg1, ctx)
    ok1 = intent1 is not None and intent1.request.name == "create_folder"
    path1 = intent1.request.arguments["path"] if intent1 else ""
    ok1 = ok1 and "Deneme2026" in str(path1) and "Hermes" not in str(path1)
    if ok1:
        result = await run_tool("create_folder", path=str(FOLDER_2026))
        ok1 = result.success and FOLDER_2026.is_dir()
        ctx.active_folder = str(FOLDER_2026)
        ctx.last_created_folder = str(FOLDER_2026)
    await check("1 create Deneme2026", ok1, f"source={source1}, path={path1}")

    # 2. Open Deneme2026 folder
    msg2 = "Deneme2026 klasorunu ac."
    intent2, source2 = resolve_intent(msg2, ctx)
    ok2 = intent2 is not None and intent2.request.name == "open_path"
    if ok2:
        result = await run_tool("open_path", path=str(FOLDER_2026))
        ok2 = result.success
    await check("2 open Deneme2026", ok2, f"source={source2}")

    # 3. Create test.txt with Merhaba
    msg3 = "Icine test.txt olustur ve icine Merhaba yaz."
    intent3, source3 = resolve_intent(msg3, ctx)
    ok3 = intent3 is not None and intent3.request.name == "write_file"
    if ok3:
        args = intent3.request.arguments
        result = await run_tool("write_file", **args)
        ok3 = (
            result.success
            and TEST_FILE.is_file()
            and TEST_FILE.read_text(encoding="utf-8") == args.get("content", "")
        )
        ctx.active_file = str(TEST_FILE)
        ctx.last_created_file = str(TEST_FILE)
    await check("3 create test.txt", ok3, f"source={source3}")

    # 4. Open test.txt
    msg4 = "test.txt dosyasini ac."
    intent4, source4 = resolve_intent(msg4, ctx)
    ok4 = intent4 is not None and intent4.request.name == "open_path"
    if ok4:
        result = await run_tool("open_path", path=str(TEST_FILE))
        ok4 = result.success
    await check("4 open test.txt", ok4, f"source={source4}")

    # 5. Open Chrome
    msg5 = "Chrome'u ac"
    intent5, source5 = resolve_intent(msg5, ctx)
    ok5 = intent5 is not None and intent5.request.name == "open_app"
    ok5 = ok5 and intent5.request.arguments.get("app") == "chrome"
    if ok5:
        result = await run_tool("open_app", app="chrome")
        ok5 = result.success
    await check("5 open Chrome", ok5, f"source={source5}, app={getattr(intent5, 'request', None) and intent5.request.arguments}")

    # 6. Open Notepad
    msg6 = "Notepad'i ac"
    intent6, source6 = resolve_intent(msg6, ctx)
    ok6 = intent6 is not None and intent6.request.name == "open_app"
    if ok6:
        result = await run_tool("open_app", app=intent6.request.arguments.get("app", "notepad"))
        ok6 = result.success
    await check("6 open Notepad", ok6, f"source={source6}")

    # 7. Verify file content TEST
    content = TEST_FILE.read_text(encoding="utf-8") if TEST_FILE.is_file() else ""
    ok7 = content == "Merhaba"
    await check("7 file content TEST", ok7, repr(content))

    # 8. Create Deneme2027
    msg8 = "Masaustunde baska bir klasor olustur, adina Deneme2027 koy."
    intent8, source8 = resolve_intent(msg8, ctx)
    ok8 = intent8 is not None and intent8.request.name == "create_folder"
    if ok8:
        result = await run_tool("create_folder", path=str(FOLDER_2027))
        ok8 = result.success and FOLDER_2027.is_dir()
    await check("8 create Deneme2027", ok8, f"source={source8}")

    failed = [label for label, ok, _ in results if not ok]
    print("\nSummary:")
    print(f"  Passed: {len(results) - len(failed)}/{len(results)}")
    if failed:
        print(f"  Failed: {', '.join(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
