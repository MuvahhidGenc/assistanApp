from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, parse_qs

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


def take_screenshot(region: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
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


CHROME_DEBUG_PORT = 9222


def _tcp_port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def open_chrome_with_debugging(url: str = "", port: int = CHROME_DEBUG_PORT) -> dict[str, Any]:
    """
    Chrome'u CDP (remote debugging) ile acar.
    Normal acik Chrome oturumuna baglanilamaz — ayri profil gerekir.
    """
    path = _resolve_app_path("chrome")
    if not path:
        raise ValueError("Chrome bulunamadi")
    cdp_url = f"http://127.0.0.1:{port}"
    if _tcp_port_open("127.0.0.1", port):
        proc = subprocess.Popen([path, url] if url else [path], shell=False) if url else None
        return {
            "app": "chrome",
            "path": path,
            "pid": proc.pid if proc else None,
            "debug_port": port,
            "cdp_url": cdp_url,
            "reused_debug": True,
            "url": url or "",
        }
    profile = Path(os.environ.get("LOCALAPPDATA", ".")) / "HermesClient" / "chrome-debug"
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if url:
        cmd.append(url)
    proc = subprocess.Popen(cmd, shell=False)
    time.sleep(1.5)
    return {
        "app": "chrome",
        "path": path,
        "pid": proc.pid,
        "debug_port": port,
        "cdp_url": cdp_url,
        "reused_debug": False,
        "url": url or "",
    }


def open_application(app: str, args: list[str] | None = None) -> dict[str, Any]:
    path = _resolve_app_path(app)
    if not path:
        raise ValueError(f"Application not found: {app}")
    cmd = [path, *(args or [])]
    proc = subprocess.Popen(cmd, shell=False)
    return {"app": app, "path": path, "pid": proc.pid, "args": args or []}


def open_url(url: str, browser: str = "") -> dict[str, Any]:
    target = url.strip()
    if not target.startswith(("http://", "https://", "file://")):
        target = f"https://{target}"
    if browser:
        path = _resolve_app_path(browser)
        if path:
            proc = subprocess.Popen([path, target], shell=False)
            return {"url": target, "browser": browser, "path": path, "pid": proc.pid}
    opened = webbrowser.open(target, new=2)
    return {"url": target, "browser": browser or "default", "opened": opened}


def normalize_youtube_url(url: str, *, autoplay: bool = True) -> str:
    target = url.strip()
    if not target.startswith(("http://", "https://")):
        target = f"https://{target.lstrip('/')}"
    match = re.search(r"youtu\.be/([\w-]{6,})", target, re.IGNORECASE)
    if match:
        target = f"https://www.youtube.com/watch?v={match.group(1)}"
    parsed = urlparse(target)
    if "youtube.com" in parsed.netloc and parsed.path == "/watch":
        params = parse_qs(parsed.query)
        video_id = (params.get("v") or [""])[0]
        if video_id:
            target = f"https://www.youtube.com/watch?v={video_id}"
            if autoplay:
                target += "&autoplay=1"
    return target


def open_youtube_with_playback(url: str) -> dict[str, Any]:
    """Open YouTube and attempt to start playback (direct link or search results)."""
    raw = url.strip()
    is_search = "results?search_query=" in raw or "search_query=" in raw
    if is_search:
        normalized = raw if raw.startswith("http") else f"https://{raw}"
    else:
        normalized = normalize_youtube_url(raw, autoplay=True)

    result = open_url(normalized, browser="chrome")
    time.sleep(5.0 if is_search else 3.5)

    try:
        focus_window("YouTube")
    except Exception:
        try:
            focus_window("Chrome")
        except Exception:
            pass

    time.sleep(0.8)
    pag = _get_pyautogui()
    clicked = False
    if is_search:
        parsed = urlparse(normalized)
        query = (parse_qs(parsed.query).get("search_query") or [""])[0]
        if query:
            try:
                click_text(query, partial=True)
                clicked = True
                time.sleep(2.0)
            except Exception:
                pass
        if not clicked:
            width, height = pag.size()
            pag.click(width // 2, int(height * 0.38))
            time.sleep(1.5)

    press_keys(["k"])
    result["url"] = normalized
    result["autoplay_attempted"] = True
    result["search"] = is_search
    result["ocr_click"] = clicked
    return result


def focus_browser_window() -> str | None:
    """Once tarayici/YouTube penceresine gec — scroll/tiklamada Hermes sohbeti kaymasin."""
    for title in ("YouTube", "Google Chrome", "Chrome", "Edge", "Mozilla Firefox", "Firefox"):
        try:
            focus_window(title)
            time.sleep(0.25)
            return title
        except ValueError:
            continue
    return None


def scroll_page(direction: str = "down", amount: int = 3) -> dict[str, Any]:
    focused = focus_browser_window()
    pag = _get_pyautogui()
    width, height = pag.size()
    x, y = width // 2, height // 2
    pag.moveTo(x, y, duration=0.12)
    delta = -max(1, int(amount)) if direction.casefold() in {"down", "asagi", "aşağı"} else max(1, int(amount))
    pag.scroll(delta)
    return {"direction": direction, "amount": amount, "clicks": delta, "x": x, "y": y, "focused": focused}


def click_text(text: str, *, partial: bool = True) -> dict[str, Any]:
    from hermes.vision import find_text_on_screen

    focused = focus_browser_window()
    match = find_text_on_screen(text, partial=partial)
    if not match:
        raise ValueError(f"Ekranda bulunamadi: {text}")
    move_mouse(int(match["x"]), int(match["y"]), duration=0.15)
    time.sleep(0.08)
    clicked = click_at(int(match["x"]), int(match["y"]))
    return {**match, **clicked, "clicked": True, "focused": focused}


def show_desktop() -> dict[str, Any]:
    press_keys(["win", "d"])
    return {"action": "show_desktop"}


def browser_nav(action: str = "back") -> dict[str, Any]:
    focus_browser_window()
    key = (action or "back").strip().casefold()
    mapping = {
        "back": ["alt", "left"],
        "geri": ["alt", "left"],
        "forward": ["alt", "right"],
        "ileri": ["alt", "right"],
        "fullscreen": ["f"],
        "tam_ekran": ["f"],
        "fullscreen_f11": ["f11"],
    }
    keys = mapping.get(key)
    if not keys:
        raise ValueError(f"Bilinmeyen browser_nav: {action}")
    press_keys(keys)
    return {"action": key, "keys": keys}


def focus_window(title: str, partial: bool = True) -> dict[str, Any]:
    gw = _get_pygetwindow()
    if partial:
        matches = [w for w in gw.getAllWindows() if title.lower() in (w.title or "").lower()]
    else:
        matches = gw.getWindowsWithTitle(title)
    if not matches:
        raise ValueError(f"Window not found: {title}")
    window = matches[0]
    if window.isMinimized:
        window.restore()
    window.activate()
    return {"title": window.title, "left": window.left, "top": window.top, "focused": True}


def type_text(text: str, interval: float = 0.02) -> dict[str, Any]:
    pag = _get_pyautogui()
    if text.isascii():
        pag.typewrite(text, interval=interval)
    else:
        pag.write(text, interval=interval)
    return {"typed_length": len(text), "interval": interval}


def press_keys(keys: list[str], presses: int = 1) -> dict[str, Any]:
    pag = _get_pyautogui()
    normalized = [k.lower().replace("ctrl", "control") for k in keys]
    for _ in range(max(presses, 1)):
        if len(normalized) <= 1:
            pag.press(normalized[0] if normalized else "")
        else:
            pag.hotkey(*normalized)
    return {"keys": keys, "presses": presses}


def click_at(x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
    pag = _get_pyautogui()
    pag.click(x, y, button=button, clicks=clicks)
    pos = pag.position()
    return {
        "x": x,
        "y": y,
        "button": button,
        "clicks": clicks,
        "cursor_after": {"x": pos.x, "y": pos.y},
    }


def move_mouse(x: int, y: int, duration: float = 0.2) -> dict[str, Any]:
    pag = _get_pyautogui()
    pag.moveTo(x, y, duration=duration)
    pos = pag.position()
    return {"x": pos.x, "y": pos.y, "duration": duration}
