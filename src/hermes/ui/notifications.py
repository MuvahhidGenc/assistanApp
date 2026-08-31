from __future__ import annotations

import sys
from typing import Any


def notify(
    title: str,
    message: str,
    *,
    success: bool = True,
    enabled: bool = True,
) -> bool:
    """Show a desktop notification. Returns False if unavailable."""
    if not enabled or not message.strip():
        return False

    if sys.platform == "win32":
        try:
            from winotify import Notification, audio

            toast = Notification(
                app_id="HERMES Windows Client",
                title=title[:64],
                msg=message[:240],
                duration="short",
            )
            toast.set_audio(audio.Default, loop=False)
            toast.show()
            return True
        except Exception:
            pass

    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=5)
        return True
    except Exception:
        return False


def notify_task_completed(summary: str, *, enabled: bool = True) -> bool:
    text = summary.strip()
    if len(text) > 200:
        text = text[:197] + "..."
    return notify(
        "HERMES — Gorev tamamlandi",
        text or "Islem bitti.",
        success=True,
        enabled=enabled,
    )


def notify_error(message: str, *, enabled: bool = True) -> bool:
    return notify(
        "HERMES — Hata",
        message.strip() or "Bilinmeyen hata",
        success=False,
        enabled=enabled,
    )
