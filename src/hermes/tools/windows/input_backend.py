from __future__ import annotations

import asyncio
import base64
import io
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Any, Callable

# Common Windows app aliases → executable names or paths
_APP_ALIASES: dict[str, list[str]] = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "chrome",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "msedge",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        "firefox",
    ],
    "notepad": ["notepad"],
    "explorer": ["explorer"],
    "cmd": ["cmd"],
    "powershell": ["powershell"],
}


def _resolve_app_path(app: str) -> str | None:
    key = app.strip().lower()
    candidates = _APP_ALIASES.get(key, [app])
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if candidate.lower().endswith(".exe"):
            exe_name = os.path.basename(candidate)
            resolved = shutil.which(exe_name)
            if resolved:
                return resolved
    return None


async def run_in_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(func, *args, **kwargs)


def _get_pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


def _get_pygetwindow():
    import pygetwindow as gw

    return gw


def take_screenshot(*, region: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
    from PIL import ImageGrab

    bbox = None
    if region:
        left, top, width, height = region
        bbox = (left, top, left + width, top + height)
    image = ImageGrab.grab(bbox=bbox)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    screenshot_dir = Path(os.environ.get("LOCALAPPDATA", ".")) / "HERMES" / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = screenshot_dir / f"screenshot_{os.getpid()}.png"
    path.write_bytes(raw)
    return {
        "path": str(path),
        "width": image.width,
        "height": image.height,
        "format": "png",
        "base64_preview": base64.b64encode(raw[:8192]).decode("ascii"),
    }


def open_application(app: str, *, args: list[str] | None = None) -> dict[str, Any]:
    path = _resolve_app_path(app)
    if not path:
        raise ValueError(f"Application not found: {app}")
    cmd = [path, *(args or [])]
    proc = subprocess.Popen(cmd, shell=False)  # noqa: S603
    return {"app": app, "path": path, "pid": proc.pid, "args": args or []}


def open_url(url: str, *, browser: str = "") -> dict[str, Any]:
    target = url.strip()
    if not target.startswith(("http://", "https://", "file://")):
        target = f"https://{target}"

    if browser:
        path = _resolve_app_path(browser)
        if path:
            proc = subprocess.Popen([path, target], shell=False)  # noqa: S603
            return {"url": target, "browser": browser, "path": path, "pid": proc.pid}

    opened = webbrowser.open(target, new=2)
    return {"url": target, "browser": browser or "default", "opened": opened}


def focus_window(title: str, *, partial: bool = True) -> dict[str, Any]:
    gw = _get_pygetwindow()
    matches = gw.getWindowsWithTitle(title)
    if not matches and partial:
        matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]
    if not matches:
        raise ValueError(f"Window not found: {title}")
    window = matches[0]
    if window.isMinimized:
        window.restore()
    window.activate()
    return {"title": window.title, "left": window.left, "top": window.top, "focused": True}


def type_text(text: str, *, interval: float = 0.02) -> dict[str, Any]:
    pag = _get_pyautogui()
    if text.isascii():
        pag.typewrite(text, interval=interval)
    else:
        pag.write(text)
    return {"typed_length": len(text), "interval": interval}


def press_keys(keys: list[str], *, presses: int = 1) -> dict[str, Any]:
    pag = _get_pyautogui()
    normalized = [k.lower().replace("ctrl", "ctrl").replace("control", "ctrl") for k in keys]
    for _ in range(max(presses, 1)):
        if len(normalized) == 1:
            pag.press(normalized[0])
        else:
            pag.hotkey(*normalized)
    return {"keys": keys, "presses": presses}


def click_at(x: int, y: int, *, button: str = "left", clicks: int = 1) -> dict[str, Any]:
    pag = _get_pyautogui()
    pag.click(x=x, y=y, button=button, clicks=clicks)
    pos = pag.position()
    return {"x": x, "y": y, "button": button, "clicks": clicks, "cursor_after": {"x": pos.x, "y": pos.y}}


def move_mouse(x: int, y: int, *, duration: float = 0.2) -> dict[str, Any]:
    pag = _get_pyautogui()
    pag.moveTo(x, y, duration=duration)
    pos = pag.position()
    return {"x": pos.x, "y": pos.y, "duration": duration}
