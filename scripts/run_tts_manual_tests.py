"""Validate TTS synthesizer output for manual voice feedback checklist."""
from __future__ import annotations

from hermes.voice.response_synthesizer import (
    is_technical_status,
    sanitize_for_tts,
    synthesize_task_completed,
    synthesize_task_started,
)

STEPS = [
    ("create folder", "Masaustunde SesTest2026 klasoru olustur.", "SesTest2026 klasorunu masaustunde olusturdum."),
    ("open folder", "Klasoru ac.", "Klasoru actim."),
    (
        "create file+content",
        "Icine test.txt olustur ve icine Merhaba yaz.",
        "Test dosyasini olusturdum ve Merhaba yazdim.",
    ),
    ("open file", "Dosyayi ac.", "Dosyayi actim."),
    ("modify content", "Icerigini sadece HERMES TEST yap.", "Ikinci dosyasini olusturdum."),
    ("open chrome", "Chrome'u ac.", "Chrome'u actim."),
    ("open notepad", "Notepad'i ac.", "Not Defteri'ni actim."),
]

FORBIDDEN = (
    "write_file",
    "open_path",
    "create_folder",
    "list_windows",
    "verification",
    "execution_target",
    "C:\\Users",
    "{",
    "}",
)


def main() -> int:
    failed: list[str] = []
    print("=== TTS MANUAL SYNTHESIS CHECK ===\n")
    for label, user_msg, response in STEPS:
        start = synthesize_task_started(user_msg) or "(fast-only / none)"
        done = synthesize_task_completed(user_msg, response) or "(none)"
        print(f"[{label}]")
        print(f"  USER: {user_msg}")
        print(f"  START TTS: {start}")
        print(f"  DONE  TTS: {done}")
        blob = f"{start} {done}".casefold()
        for token in FORBIDDEN:
            if token.casefold() in blob:
                failed.append(f"{label}: forbidden token {token}")
        if is_technical_status(response):
            failed.append(f"{label}: response marked technical")
        print()

    internal = [
        "Anladim, isleme basliyorum...",
        "Kontrol ediyorum...",
        "Yerel tool calistiriliyor: write_file",
    ]
    print("=== INTERNAL STATUS (must not speak) ===")
    for status in internal:
        spoken = synthesize_task_completed("", status)
        ok = spoken is None or is_technical_status(status)
        print(f"  {status!r} -> TTS={spoken!r} OK={ok}")
        if not ok:
            failed.append(f"internal status spoken: {status}")

    print(f"\nSUMMARY: {len(STEPS) - len([f for f in failed if not f.startswith('internal')])}/{len(STEPS)} scenarios clean")
    if failed:
        print("FAILED:")
        for item in failed:
            print(f"  - {item}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
