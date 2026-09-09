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

from hermes.utils.logging import get_logger

logger = get_logger(__name__)

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
    "calc": ["calc"],
    "mspaint": ["mspaint", "paint"],
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


_NEW_WINDOW_MARKERS = (
    "yeni pencere",
    "yeni chrome",
    "yeni notepad",
    "yeni not defteri",
    "new window",
    "yeni instance",
    "ayri pencere",
    "ayrı pencere",
)

_RECREATE_MARKERS = (
    "yeniden olustur",
    "yeniden oluştur",
    "tekrar olustur",
    "tekrar oluştur",
)

_APP_WINDOW_HINTS: dict[str, tuple[str, ...]] = {
    "chrome": ("google chrome", "chrome"),
    "edge": ("microsoft edge", "edge"),
    "firefox": ("mozilla firefox", "firefox"),
    "notepad": ("notepad", "not defteri"),
}


def wants_new_window(user_message: str = "", *, args: list[str] | None = None) -> bool:
    lower = (user_message or "").casefold()
    if any(marker in lower for marker in _NEW_WINDOW_MARKERS):
        return True
    if args and any(arg in ("--new-window", "-new-window") for arg in args):
        return True
    return False


def wants_recreate(user_message: str = "") -> bool:
    lower = (user_message or "").casefold()
    return any(marker in lower for marker in _RECREATE_MARKERS)


def wants_modify_existing(user_message: str = "") -> bool:
    """True when the user intends to change content of an existing file."""
    lower = (user_message or "").casefold()
    markers = (
        "degistir",
        "değiştir",
        "guncelle",
        "güncelle",
        "icerigini",
        "içeriğini",
        "olarak yap",
        "olarak yaz",
    )
    return any(marker in lower for marker in markers)


def list_window_titles() -> list[str]:
    gw = _get_pygetwindow()
    return [str(window.title).strip() for window in gw.getAllWindows() if str(window.title).strip()]


def _title_matches_app(title: str, app_key: str) -> bool:
    lower = title.casefold()
    hints = _APP_WINDOW_HINTS.get(app_key.casefold(), (app_key.casefold(),))
    if not any(hint in lower for hint in hints):
        return False
    if app_key == "chrome" and "hermes" in lower:
        return False
    return True


def find_app_window_title(app: str, *, title_hint: str = "") -> str | None:
    key = app.strip().casefold()
    candidates = [title for title in list_window_titles() if _title_matches_app(title, key)]
    if not candidates:
        return None
    hint = (title_hint or "").strip().casefold()
    if not hint:
        return candidates[0]

    def score(title: str) -> tuple[int, int]:
        lower = title.casefold()
        if hint in lower:
            return (0, -len(title))
        hint_tokens = [token for token in re.split(r"[\s\-—|]+", hint) if len(token) >= 3]
        token_hits = sum(1 for token in hint_tokens if token in lower)
        return (1 if token_hits else 2, -token_hits)

    return sorted(candidates, key=score)[0]


def _normalize_path(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _explorer_title_matches_folder(title: str, folder: Path) -> bool:
    folder_name = folder.name.casefold()
    lower = title.casefold()
    if folder_name not in lower:
        return False
    if lower.strip() == folder_name:
        return True
    explorer_markers = ("file explorer", "dosya gezgini", "explorer")
    return any(marker in lower for marker in explorer_markers)


def find_explorer_window_for_folder(folder_path: Path) -> str | None:
    folder = _normalize_path(folder_path)
    for title in list_window_titles():
        if _explorer_title_matches_folder(title, folder):
            return title
    return None


def find_file_window_title(file_path: Path) -> str | None:
    stem = file_path.stem.casefold()
    name = file_path.name.casefold()
    for title in list_window_titles():
        lower = title.casefold()
        if name not in lower and (not stem or stem not in lower):
            continue
        if any(marker in lower for marker in ("notepad", "not defteri", "word", "excel", "editor")):
            return title
    return None


def open_path_on_windows(
    path: str | Path,
    *,
    user_message: str = "",
    force_new: bool = False,
) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.is_absolute():
        from hermes.context.system_paths import current_user_desktop_path

        desktop = current_user_desktop_path()
        if desktop is None:
            raise ValueError("Current user Desktop path could not be observed")
        target = desktop / target
    if not target.exists():
        raise ValueError(f"Yol bulunamadi: {target}")

    new_window = force_new or wants_new_window(user_message)
    if not new_window:
        if target.is_dir():
            existing = find_explorer_window_for_folder(target)
            if existing:
                logger.info("open_path_reuse_explorer", path=str(target), title=existing)
                focus_window(existing, partial=True)
                return {
                    "path": str(target),
                    "opened": False,
                    "reused": True,
                    "verified": True,
                    "is_directory": True,
                    "window_title": existing,
                }
        else:
            existing = find_file_window_title(target)
            if existing:
                logger.info("open_path_reuse_app", path=str(target), title=existing)
                focus_window(existing, partial=True)
                return {
                    "path": str(target),
                    "opened": False,
                    "reused": True,
                    "verified": True,
                    "is_directory": False,
                    "window_title": existing,
                }

    os.startfile(str(target))  # noqa: S606
    logger.info("open_path_launched", path=str(target), is_directory=target.is_dir())
    verified = False
    window_title: str | None = None
    if target.is_dir():
        for _ in range(8):
            time.sleep(0.35)
            window_title = find_explorer_window_for_folder(target)
            if window_title:
                verified = True
                logger.info("open_path_explorer_detected", path=str(target), title=window_title)
                break
    elif target.is_file():
        for _ in range(6):
            time.sleep(0.3)
            window_title = find_file_window_title(target)
            if window_title:
                verified = True
                logger.info("open_path_app_detected", path=str(target), title=window_title)
                break
    return {
        "path": str(target),
        "opened": True,
        "reused": False,
        "verified": verified,
        "is_directory": target.is_dir(),
        "window_title": window_title,
    }


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


def open_application(
    app: str,
    args: list[str] | None = None,
    *,
    user_message: str = "",
    force_new: bool = False,
) -> dict[str, Any]:
    path = _resolve_app_path(app)
    if not path:
        raise ValueError(f"Application not found: {app}")
    app_key = app.strip().casefold()
    new_window = force_new or wants_new_window(user_message, args=args)

    if not new_window and app_key in _APP_WINDOW_HINTS:
        existing = find_app_window_title(app_key)
        if existing:
            logger.info("open_app_reuse_window", app=app_key, title=existing)
            try:
                focus_window(existing, partial=False)
            except ValueError:
                focus_window(existing, partial=True)
            return {
                "app": app,
                "path": path,
                "reused": True,
                "focused": True,
                "verified": True,
                "window_title": existing,
            }

    cmd = [path, *(args or [])]
    proc = subprocess.Popen(cmd, shell=False)
    logger.info("open_app_launched", app=app_key, pid=proc.pid)
    time.sleep(0.8)
    window_title = find_app_window_title(app_key)
    return {
        "app": app,
        "path": path,
        "pid": proc.pid,
        "args": args or [],
        "reused": False,
        "verified": bool(window_title),
        "window_title": window_title,
    }


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


def _focus_window_hwnd(hwnd: int) -> None:
    import win32con
    import win32gui

    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def capture_window_image(
    title: str,
    *,
    partial: bool = True,
    chrome_crop: int = 96,
) -> dict[str, Any]:
    """
    Capture a window via PrintWindow (works for Chrome/DWM layered windows).
    ImageGrab on window regions often returns blank for unfocused Chrome.
    """
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    gw = _get_pygetwindow()
    if partial:
        matches = [w for w in gw.getAllWindows() if title.lower() in (w.title or "").lower()]
    else:
        matches = gw.getWindowsWithTitle(title)
    if not matches:
        raise ValueError(f"Window not found: {title}")
    window = matches[0]
    hwnd = int(window._hWnd)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = max(int(right - left), 1)
    height = max(int(bottom - top), 1)

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    save_bitmap = win32ui.CreateBitmap()
    save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(save_bitmap)
    pw_result = int(ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3))
    bmp_info = save_bitmap.GetInfo()
    bmp_bits = save_bitmap.GetBitmapBits(True)
    image = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        bmp_bits,
        "raw",
        "BGRX",
        0,
        1,
    )
    win32gui.DeleteObject(save_bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    crop_top = min(max(int(chrome_crop), 0), max(image.height - 1, 0))
    client_image = image.crop((0, crop_top, image.width, image.height))

    screenshot_dir = Path(os.environ.get("LOCALAPPDATA", ".")) / "HERMES" / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = screenshot_dir / f"window_{os.getpid()}_{hwnd}.png"
    client_image.save(path, format="PNG")

    return {
        "path": str(path),
        "width": client_image.width,
        "height": client_image.height,
        "format": "png",
        "capture_method": "printwindow",
        "printwindow_ok": bool(pw_result),
        "window_title": str(window.title or title),
        "window_rect": (left, top, width, height),
        "hwnd": hwnd,
        "chrome_crop": crop_top,
    }


def focus_window(title: str, partial: bool = True) -> dict[str, Any]:
    gw = _get_pygetwindow()
    if partial:
        matches = [w for w in gw.getAllWindows() if title.lower() in (w.title or "").lower()]
    else:
        matches = gw.getWindowsWithTitle(title)
    if not matches:
        raise ValueError(f"Window not found: {title}")
    window = matches[0]
    focused = False
    try:
        if window.isMinimized:
            window.restore()
        window.activate()
        focused = True
    except Exception as exc:
        logger.info("focus_window_pygetwindow_skip", title=title, error=str(exc))
        try:
            _focus_window_hwnd(int(window._hWnd))
            focused = True
        except Exception as inner:
            logger.info("focus_window_win32_skip", title=title, error=str(inner))
    return {
        "title": window.title,
        "left": window.left,
        "top": window.top,
        "focused": focused,
        "hwnd": int(window._hWnd),
    }


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
