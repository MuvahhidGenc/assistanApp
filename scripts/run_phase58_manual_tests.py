"""Run exact 7-step PHASE 5.8 manual scenario."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import MagicMock

from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext


DESKTOP = Path.home() / "Desktop"
FOLDER = DESKTOP / "Deneme2026"


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
        ("1 create folder", "Masaustune Deneme2026 adli bir klasor olustur."),
        ("2 open folder", "Deneme2026 klasorunu ac."),
        ("3 empty file", "Icine test.txt olustur"),
        ("4 open last file", "Son olusturdugun dosyayi ac."),
        ("5 file+content", "Icine ikinci.txt olustur ve icine 12345 yaz."),
        ("6 open last file", "Son olusturdugun dosyayi ac."),
        ("7 modify content", "Icerigini sadece TEST yap."),
    ]

    failed: list[str] = []
    for label, msg in steps:
        ctx = ConversationalContext.load()
        text = await orch.process_message(msg)
        ctx = ConversationalContext.load()
        print(f"\n=== {label} ===")
        print(f"MSG: {msg}")
        print(f"RESP: {text[:120]}")
        print(f"active_folder={ctx.active_folder}")
        print(f"last_created_folder={ctx.last_created_folder}")
        print(f"active_file={ctx.active_file}")
        print(f"last_created_file={ctx.last_created_file}")

        ok = True
        if "anlayamadim" in text.casefold() and label == "3 empty file":
            ok = False
        if label == "3 empty file" and not (FOLDER / "test.txt").is_file():
            ok = False
        if label == "3 empty file" and ctx.active_folder and "Deneme2026" not in ctx.active_folder:
            ok = False
        if label == "4 open last file" and ctx.last_created_file and not str(ctx.last_created_file).endswith("test.txt"):
            ok = False
        if label == "5 file+content":
            p = FOLDER / "ikinci.txt"
            ok = p.is_file() and p.read_text(encoding="utf-8") == "12345"
        if label == "6 open last file" and ctx.last_created_file and not str(ctx.last_created_file).endswith("ikinci.txt"):
            ok = False
        if label == "7 modify content":
            ok = (FOLDER / "ikinci.txt").read_text(encoding="utf-8") == "TEST"

        print(f"RESULT: {'PASS' if ok else 'FAIL'}")
        if not ok:
            failed.append(label)

    print(f"\nSUMMARY: {len(steps)-len(failed)}/{len(steps)} passed")
    if failed:
        print("FAILED:", ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
