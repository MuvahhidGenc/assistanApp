from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

from hermes.tools.base import ToolExecutionResult
from hermes.tools.manifest import LocalToolRequest


@dataclass(frozen=True)
class LocalIntent:
    request: LocalToolRequest
    summary_hint: str = ""


_DNS_PRESETS: dict[str, list[str]] = {
    "google": ["8.8.8.8", "8.8.4.4"],
    "cloudflare": ["1.1.1.1", "1.0.0.1"],
    "opendns": ["208.67.222.222", "208.67.220.220"],
}


def _normalize(text: str) -> str:
    lowered = text.strip().lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _extract_ips(text: str) -> list[str]:
    return re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)


def _match_dns(text: str) -> LocalIntent | None:
    norm = _normalize(text)
    if "dns" not in norm:
        return None
    if not any(k in norm for k in ("degistir", "ayarla", "yap", "kur", "guncelle")):
        if not _extract_ips(text):
            return None
    ips = _extract_ips(text)
    if ips:
        return LocalIntent(
            LocalToolRequest(name="set_dns", arguments={"servers": ips}),
            summary_hint="dns",
        )
    for preset in _DNS_PRESETS:
        if preset in norm:
            return LocalIntent(
                LocalToolRequest(name="set_dns", arguments={"preset": preset}),
                summary_hint="dns",
            )
    return LocalIntent(
        LocalToolRequest(name="set_dns", arguments={}),
        summary_hint="dns",
    )


def _match_open_url(text: str) -> LocalIntent | None:
    norm = _normalize(text)
    url_match = re.search(r"https?://[^\s\"']+", text, re.IGNORECASE)
    if url_match:
        return LocalIntent(LocalToolRequest(name="open_url", arguments={"url": url_match.group(0)}))

    if "youtube" in norm:
        tevhid_match = re.search(
            r"youtube(?:['']dan)?\s+(.+?)\s+(?:ile\s+)?(?:aramaya|ara|search)",
            norm,
        )
        if tevhid_match:
            query = tevhid_match.group(1).strip(" '\"")
            query = re.sub(r"\b(ile|icin|video|ac|bir)\b", "", query).strip()
            if query:
                url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
                return LocalIntent(LocalToolRequest(name="open_url", arguments={"url": url}))

        search_match = re.search(r"(?:ara|aramaya|search)\s+(.+?)(?:\s+ve\s+|\s*$)", norm)
        if search_match:
            query = search_match.group(1).strip(" '\"")
            query = re.sub(r"\b(video|ac|izle|bir)\b", "", query).strip()
            if query:
                url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
                return LocalIntent(LocalToolRequest(name="open_url", arguments={"url": url}))
        return LocalIntent(
            LocalToolRequest(name="open_url", arguments={"url": "https://www.youtube.com"})
        )

    return None


def _match_open_app(text: str) -> LocalIntent | None:
    norm = _normalize(text)
    apps = {
        "chrome": r"\bchrome\b",
        "edge": r"\bedge\b",
        "firefox": r"\bfirefox\b",
        "notepad": r"\bnotepad\b",
    }
    if not re.search(r"\b(ac|baslat|calistir|open)\b", norm):
        return None
    for app, pattern in apps.items():
        if re.search(pattern, norm):
            return LocalIntent(LocalToolRequest(name="open_app", arguments={"app": app}))
    return None


def _match_create_folder(text: str) -> LocalIntent | None:
    norm = _normalize(text)
    if not re.search(r"\bklasor|\bfolder\b", norm):
        return None
    if not re.search(r"\b(olustur|yarat|create)\b", norm):
        return None
    name_match = re.search(
        r"(?:masaustunde|masaustu|desktop)\s+(\w+)\s+klasor(?:u|unu)?",
        norm,
    )
    folder_name = name_match.group(1) if name_match else "YeniKlasor"
    path = f"{folder_name}"
    if "masaust" in norm or "desktop" in norm:
        path = f"Desktop/{folder_name}"
    return LocalIntent(
        LocalToolRequest(name="create_folder", arguments={"path": path}),
    )


def match_local_intent(text: str) -> LocalIntent | None:
    """Match a single-step local tool intent from Turkish user text."""
    if not text or not text.strip():
        return None

    norm = _normalize(text)

    if re.search(r"\b(az once|once ne dedim|hatirla|nasilsin|merhaba)\b", norm):
        if "dns" not in norm and "chrome" not in norm:
            return None

    if re.search(r"\b(dns|nameserver)\b", norm):
        intent = _match_dns(text)
        if intent:
            return intent

    if re.search(r"\s+ve\s+", norm) and "dns" not in norm and "youtube" not in norm:
        return None

    for phrase in (
        "ekrani oku",
        "ekrana bak",
        "ne goruyorsun",
        "ekranda ne yaziyor",
    ):
        if phrase in norm:
            return LocalIntent(LocalToolRequest(name="read_screen_text", arguments={}))

    if re.search(r"\b(ekran\s*goruntusu|screenshot|ss\s*al)\b", norm):
        return LocalIntent(LocalToolRequest(name="screenshot", arguments={}))

    if re.search(r"\b(ip\s*adres|ag\s*ayar|network)\b", norm) or "ip adresimi" in norm:
        return LocalIntent(LocalToolRequest(name="get_network_config", arguments={}))

    if re.search(r"\basagi\s+in\b", norm):
        return LocalIntent(
            LocalToolRequest(name="scroll", arguments={"direction": "down"}),
        )

    if "masaustunu goster" in norm or "show desktop" in norm:
        return LocalIntent(LocalToolRequest(name="show_desktop", arguments={}))

    if re.search(r"\bgeri\s+git\b", norm):
        return LocalIntent(
            LocalToolRequest(name="browser_nav", arguments={"action": "back"}),
        )

    url_intent = _match_open_url(text)
    if url_intent:
        return url_intent

    video_match = re.search(r"(\w+)\s+videosunu\s+ac", text, re.IGNORECASE)
    if video_match and video_match.group(1).lower() not in {"youtube", "netflix", "spotify"}:
        title = video_match.group(1)
        return LocalIntent(
            LocalToolRequest(name="click_text", arguments={"text": title}),
        )

    folder_intent = _match_create_folder(text)
    if folder_intent:
        return folder_intent

    app_intent = _match_open_app(text)
    if app_intent:
        return app_intent

    return None


def guess_file_action(text: str) -> LocalIntent | None:
    norm = _normalize(text)
    delete_match = re.search(r"(\w+)\s+klasor(?:u|unu)?\s+sil", norm)
    if delete_match or (re.search(r"\b(sil|delete|kaldir)\b", norm) and re.search(r"\b(klasor|folder)\b", norm)):
        name_match = re.search(r"(\w+)\s+klasor(?:u|unu)?", norm)
        folder = name_match.group(1) if name_match else "Hermes"
        return LocalIntent(
            LocalToolRequest(
                name="delete_path",
                arguments={"path": folder, "recursive": True},
            ),
        )
    if "word" in norm or "docx" in norm:
        topic = "tevhid"
        if "tevhid" in norm:
            topic = "tevhid"
        content = (
            f"{topic.title()} ile ilgili detayli bilgi.\n\n"
            f"{topic.title()}, Islam'in temel inanc esaslarindan biridir."
        )
        return LocalIntent(
            LocalToolRequest(
                name="create_word_document",
                arguments={
                    "path": f"Hermes2/{topic}.docx",
                    "title": topic.title(),
                    "content": content,
                },
            ),
        )
    return None


def guess_install_action(text: str) -> LocalIntent | None:
    norm = _normalize(text)
    if not re.search(r"\b(kur|install|yukle)\b", norm):
        return None
    packages = {
        "libreoffice": r"\blibre\s*office\b|\blibreoffice\b",
        "chrome": r"\bchrome\b",
        "firefox": r"\bfirefox\b",
        "vscode": r"\bvscode\b|\bvisual\s*studio\s*code\b",
    }
    for package, pattern in packages.items():
        if re.search(pattern, norm):
            return LocalIntent(
                LocalToolRequest(
                    name="install_program",
                    arguments={"package": package},
                ),
            )
    return None


def summarize_local_result(intent: LocalIntent, result: ToolExecutionResult) -> str:
    if not result.success:
        return f"Islem basarisiz: {result.error or 'bilinmeyen hata'}"

    if intent.request.name == "set_dns":
        output = result.output if isinstance(result.output, dict) else {}
        servers = output.get("servers") or []
        adapter = output.get("adapter", "")
        parts = ["DNS guncellendi."]
        if servers:
            parts.append(f"Sunucular: {', '.join(servers)}.")
        elif intent.request.arguments.get("preset"):
            preset = intent.request.arguments["preset"]
            servers = _DNS_PRESETS.get(preset, [])
            if servers:
                parts.append(f"Sunucular: {', '.join(servers)}.")
        if adapter:
            parts.append(f"Adapter: {adapter}.")
        return " ".join(parts)

    if intent.request.name == "open_app":
        app = (result.output or {}).get("app", intent.request.arguments.get("app", "uygulama"))
        return f"{app.title()} acildi."

    if intent.request.name == "open_url":
        url = (result.output or {}).get("url", intent.request.arguments.get("url", ""))
        if "youtube" in str(url).lower():
            return "YouTube acildi."
        return f"Adres acildi: {url}"

    if intent.request.name == "read_screen_text":
        output = result.output if isinstance(result.output, dict) else {}
        text = output.get("text", "")
        if text:
            return f"Ekranda gorunen yazi:\n{text}"
        return "Ekranda okunabilir yazi bulunamadi."

    if intent.request.name == "create_folder":
        path = (result.output or {}).get("path", "")
        return f"Klasor olusturuldu: {path}"

    if intent.request.name == "screenshot":
        return "Ekran goruntusu alindi."

    if intent.request.name == "get_network_config":
        return "Ag yapilandirmasi alindi."

    output_str = str(result.output) if result.output else "Tamamlandi."
    return output_str[:500]
