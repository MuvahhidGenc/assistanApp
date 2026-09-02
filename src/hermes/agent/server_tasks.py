from __future__ import annotations

import re

from hermes.agent.task_planner import is_multi_step_message

_DEFER_KEYWORDS = (
    "indir",
    "download",
    "kur",
    "install",
    "yukle",
    "yükle",
    "klon",
    "clone",
    "github",
    "gitlab",
    "bitbucket",
    "repom",
    "repodan",
    "repo ",
    " repom",
    "proje cek",
    "projeyi cek",
    "projeyi indir",
    "git pull",
    "git clone",
    "npm install",
    "pip install",
    "winget",
    "setup.exe",
    "installer",
    "masaustunu duzenle",
    "masaüstünü düzenle",
    "hizlandir",
    "hızlandır",
    "derle",
    "build al",
    "compile",
)

_REPO_HOST = re.compile(r"github\.com|gitlab\.com|bitbucket\.org", re.IGNORECASE)
_DOWNLOAD_EXT = re.compile(r"\.(exe|msi|zip|7z|rar|dmg|pkg|deb|msix)(?:\?|$)", re.IGNORECASE)


def _is_local_browser_task(message: str) -> bool:
    """YouTube / tarayici acma islemleri yerelde open_url ile yapilir — browser-use kullanma."""
    lower = (message or "").casefold()
    if any(keyword in lower for keyword in _DEFER_KEYWORDS):
        return False
    return bool(
        re.search(
            r"youtube|youtu\.be|\bchrome\b|tarayici|tarayıcı|\bbrowser\b|google\.com",
            lower,
        )
    )


def _is_interactive_desktop_task(message: str) -> bool:
    lower = (message or "").casefold()
    return bool(
        re.search(
            r"asagi|aşağı|yukari|yukarı|kaydir|kaydır|scroll|tikla|tıkla|masa[uü]st|"
            r"geri\s*git|tam\s*ekran|buyuk\s*ekran|büyük\s*ekran|ekranda|sonuc|sonuç|"
            r"videoyu|videosunu|fullscreen",
            lower,
        )
    )


def should_defer_to_server(message: str) -> bool:
    """Complex workflows go to Hermes server for multi-step LOCAL_TOOL planning."""
    text = (message or "").strip()
    if not text:
        return False
    lower = text.casefold()

    from hermes.agent.general_goal import is_general_mission_goal

    if is_general_mission_goal(text):
        return False

    if _is_local_browser_task(text) or _is_interactive_desktop_task(text):
        return False

    if any(keyword in lower for keyword in _DEFER_KEYWORDS):
        return True
    if _REPO_HOST.search(text):
        return True
    if _DOWNLOAD_EXT.search(text):
        return True
    if is_multi_step_message(text) and len(text) > 35:
        return True
    if lower.count(" ve ") >= 2 or lower.count(" sonra ") >= 1:
        return True
    return False
