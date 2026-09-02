"""Manual Windows scenario: idempotent app/window ops + filesystem background + TTS."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import MagicMock

from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.tools.windows import input_backend as ib


DESKTOP = Path.home() / "Desktop"
FOLDER = DESKTOP / "Test2026"


async def main() -> int:
    if FOLDER.exists():
        shutil.rmtree(FOLDER, ignore_errors=True)

    ctx = ConversationalContext.load()
    ctx.active_folder = None
    ctx.last_created_folder = None
    ctx.active_file = None
    ctx.last_created_file = None
    ctx.recent_files = []
    ctx.save()

    orch = AgentOrchestrator(AppSettings(), MagicMock())
    import hermes.client.session_store as ss

    ss.append_conversation_turn = lambda *a, **k: None

    steps = [
        "Chrome'u ac.",
        "Chrome'u ac.",
        "Notepad'i ac.",
        "Notepad'i ac.",
        "Masaustunde Test2026 klasoru olustur.",
        "Test2026 klasorunu ac.",
        "Test2026 klasorunu ac.",
        "Icine test.txt olustur ve icine Merhaba yaz.",
        "Icine ikinci.txt olustur ve icine 12345 yaz.",
        "Son olusturdugun dosyayi ac.",
    ]

    chrome_launches = 0
    notepad_launches = 0
    explorer_launches = 0
    original_open_app = ib.open_application
    original_open_path = ib.open_path_on_windows

    def counting_open_app(*args, **kwargs):
        nonlocal chrome_launches, notepad_launches
        result = original_open_app(*args, **kwargs)
        app = str(kwargs.get("app") or (args[0] if args else "")).casefold()
        if not result.get("reused"):
            if app == "chrome":
                chrome_launches += 1
            elif app == "notepad":
                notepad_launches += 1
        return result

    def counting_open_path(*args, **kwargs):
        nonlocal explorer_launches
        result = original_open_path(*args, **kwargs)
        if result.get("opened") and result.get("is_directory"):
            explorer_launches += 1
        return result

    ib.open_application = counting_open_app  # type: ignore[assignment]
    ib.open_path_on_windows = counting_open_path  # type: ignore[assignment]

    failed: list[str] = []
    for index, msg in enumerate(steps, start=1):
        text = await orch.process_message(msg)
        ctx = ConversationalContext.load()
        print(f"\n=== Step {index} ===")
        print(f"MSG: {msg}")
        print(f"RESP: {text[:140]}")
        print(f"active_folder={ctx.active_folder}")
        print(f"last_created_file={ctx.last_created_file}")

    ib.open_application = original_open_app  # type: ignore[assignment]
    ib.open_path_on_windows = original_open_path  # type: ignore[assignment]

    checks = [
        ("test.txt content", (FOLDER / "test.txt").is_file() and (FOLDER / "test.txt").read_text(encoding="utf-8") == "Merhaba"),
        ("ikinci.txt content", (FOLDER / "ikinci.txt").is_file() and (FOLDER / "ikinci.txt").read_text(encoding="utf-8") == "12345"),
        ("chrome launches <= 1", chrome_launches <= 1),
        ("notepad launches <= 1", notepad_launches <= 1),
        ("explorer launches <= 1", explorer_launches <= 1),
    ]
    print("\n=== CHECKS ===")
    for label, ok in checks:
        print(f"{label}: {'PASS' if ok else 'FAIL'}")
        if not ok:
            failed.append(label)

    print(f"\nlaunch counts: chrome={chrome_launches}, notepad={notepad_launches}, explorer={explorer_launches}")
    print(f"SUMMARY: {len(checks)-len(failed)}/{len(checks)} checks passed")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
