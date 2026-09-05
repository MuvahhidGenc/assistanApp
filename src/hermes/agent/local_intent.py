from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from pathlib import Path

from hermes.tools.manifest import LocalToolRequest, _looks_like_local_filename, extract_url_hint

_TEVHID_CONTENT = (
    "Tevhid, Islam inancinin temelidir: Allah Teala'nin zat, sifat, fiil ve ibadetlerde "
    "tek ve esisiz olduguna inanmaktir.\n\n"
    "Rububiyet Tevhidi: Yaratma, rızık verme, hayat ve olumu yalnizca Allah'a aittir.\n\n"
    "Uluhiyet Tevhidi: Ibade ve dua yalnizca Allah'a yoneltilir; sevk ve yardim isteme de "
    "Allah'tan istenir.\n\n"
    "Esma ve Sifat Tevhidi: Allah'in guzel isimleri ve nitelikleri hakkında Kur'an ve "
    "Sahih Sunnet'in bildirdigi sekilde inanmak; tefvil ve teshbihden kacinmak.\n\n"
    "Kaynaklar: Kur'an-ı Kerim (112. Fatiha, 2:255 Ayetul-Kursi), Sahih hadisler ve "
    "Selefi salihin aciklamalari."
)

@dataclass(frozen=True)
class LocalIntent:
    request: LocalToolRequest
    summary: str


_DNS_PRESETS = {
    "google": "google",
    "8.8.8.8": "google",
    "cloudflare": "cloudflare",
    "1.1.1.1": "cloudflare",
    "quad9": "quad9",
    "9.9.9.9": "quad9",
    "dhcp": "dhcp",
    "otomatik": "dhcp",
}


def match_local_intent(message: str) -> LocalIntent | None:
    """Map clear PC commands to local tools. None means ask Hermes."""
    text = (message or "").strip()
    if not text:
        return None

    from hermes.intent.turn_relation import has_negative_polarity

    if has_negative_polarity(text):
        return None

    from hermes.agent.server_tasks import should_defer_to_server
    from hermes.agent.task_planner import is_multi_step_message, plan_local_sequence

    if should_defer_to_server(text):
        return None

    from hermes.screen.reference import is_screen_perception_task

    if is_screen_perception_task(text) and not extract_url_hint(text) and not _extract_youtube_url(text):
        return None

    lower = text.lower()

    if extract_url_hint(text) or _extract_youtube_url(text):
        video = _match_video_intent(text, lower)
        if video:
            return video

    if re.search(r"youtube|youtu", lower) and re.search(r"\b(ara|aram|ac|aç|izle|oynat)\b", lower):
        video = _match_video_intent(text, lower)
        if video:
            return video

    interactive = _match_interactive_control(text, lower)
    if interactive:
        return interactive

    video = _match_video_intent(text, lower)
    if video:
        return video

    if is_multi_step_message(text) and len(plan_local_sequence(text)) >= 2:
        return None

    if re.search(r"\b(dns)\b", lower) and re.search(
        r"degistir|değiştir|ayarla|yap|kur|set|change", lower
    ):
        ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
        if ips:
            return LocalIntent(
                LocalToolRequest(name="set_dns", arguments={"servers": ips}),
                summary=f"DNS {', '.join(ips)} olarak ayarlanacak.",
            )
        preset = "google"
        for key, value in _DNS_PRESETS.items():
            if key in lower:
                preset = value
                break
        return LocalIntent(
            LocalToolRequest(name="set_dns", arguments={"preset": preset}),
            summary=f"DNS {preset} olarak ayarlanacak.",
        )

    if _wants_screen_read(lower):
        return LocalIntent(LocalToolRequest("read_screen_text", {}), "Ekrandaki yazi okunacak.")
    if re.search(r"ekran\s*(gorunt|görünt)|screenshot", lower):
        return LocalIntent(LocalToolRequest("screenshot", {}), "Ekran goruntusu alinacak.")

    if re.search(r"sistem\s*(bilgi|durum)|pc\s*bilgi|bilgisayar\s*bilgi|cpu|ram kullan", lower):
        return LocalIntent(LocalToolRequest("get_system_info", {}), "Sistem bilgisi okunacak.")
    if re.search(r"\bdisk\b", lower) and re.search(r"bilgi|durum|kullanim|kullanım|goster|göster", lower):
        return LocalIntent(LocalToolRequest("get_disk_info", {}), "Disk bilgisi okunacak.")

    if re.search(r"pencere(leri)?\s*(listele|goster|göster)|list windows", lower):
        return LocalIntent(LocalToolRequest("list_windows", {}), "Acik pencereler listelenecek.")

    if re.search(r"ses(i)?\s*(kapat|sustur|mute)", lower):
        return LocalIntent(LocalToolRequest("set_volume", {"action": "mute"}), "Ses kapatilacak.")
    if re.search(r"ses(i)?\s*(ac|aç|unmute)", lower):
        return LocalIntent(LocalToolRequest("set_volume", {"action": "unmute"}), "Ses acilacak.")
    if re.search(r"ses(i)?\s*(yukselt|yükselt|arttir|artır)", lower):
        return LocalIntent(LocalToolRequest("set_volume", {"action": "up"}), "Ses yukseltilecek.")
    if re.search(r"ses(i)?\s*(kis|kıs|azalt)", lower):
        return LocalIntent(LocalToolRequest("set_volume", {"action": "down"}), "Ses kisilacak.")

    if re.search(r"pano(yu)?\s*(oku|goster|göster)|clipboard", lower):
        return LocalIntent(LocalToolRequest("get_clipboard", {}), "Pano okunacak.")

    if re.search(r"ag|ağ|ip\s*(adres|config)|dns\s*(nedir|ne|goster|göster)|wifi", lower) and not re.search(
        r"degistir|değiştir|ayarla", lower
    ):
        if re.search(r"ip|dns|ag|ağ|wifi|ethernet|network", lower):
            return LocalIntent(LocalToolRequest("get_network_config", {}), "Ag bilgisi okunacak.")

    folder = _match_create_folder(text, lower)
    if folder:
        return folder

    from hermes.agent.application_catalog import resolve_application, resolve_web_url

    web_url = resolve_web_url(text)
    if web_url:
        return LocalIntent(
            LocalToolRequest("open_url", {"url": web_url}),
            summary=f"{web_url} acilacak.",
        )
    app = resolve_application(text)
    if app:
        return LocalIntent(
            LocalToolRequest("open_app", {"app": app}),
            summary=f"{app} acilacak.",
        )

    opened = _match_open_path(text, lower)
    if opened:
        return opened
    install = _match_install_program(text, lower)
    if install:
        return install
    if re.search(r"https?://|www\.|\.com|\.net|\.org|git\b", lower):
        url = extract_url_hint(text) or _extract_youtube_url(text)
        if url and not _looks_like_local_filename(url):
            href = url if "://" in url else f"https://{url}"
            autoplay = "youtube" in href.lower() or "youtu.be" in href.lower()
            args = {"url": href}
            if autoplay:
                args["autoplay"] = True
            return LocalIntent(LocalToolRequest("open_url", args), f"{href} acilacak.")
    return None


def guess_install_action(message: str) -> LocalIntent | None:
    """App install requests run locally via winget — never wait for VPS run_command."""
    text = (message or "").strip()
    if not text:
        return None
    lower = text.casefold()
    if not re.search(r"\b(kur|install|yukle|yükle)\b", lower):
        return None
    if re.search(r"\b(word|docx|dns|klasor|klasör|repo|github)\b", lower):
        return None

    package = ""
    # libreoffice i kur / libreoffice'i kur
    spaced = re.search(
        r"\b([\w\d._+-]+)\s+(?:i|ı|u|ü)\s+(?:kur|install|yukle|yükle)\b",
        lower,
    )
    if spaced:
        package = spaced.group(1).strip(" .")
    if not package:
        suffix = re.search(
            r"\b([\w\d._+-]+)(?:'|’|`)?(?:yi|yı|yu|yü|i|ı|u)\s+(?:kur|install|yukle|yükle)\b",
            lower,
        )
        if suffix:
            package = suffix.group(1).strip(" .")
    if not package:
        trailing = re.search(
            r"\b([\w\d._+-]+)\s+(?:kur|install|yukle|yükle)\b",
            lower,
        )
        if trailing:
            package = trailing.group(1).strip(" .")
    if not package:
        leading = re.search(r"\b(?:kur|install|yukle|yükle)\s+([\w\d._+-]+)", lower)
        if leading:
            package = leading.group(1).strip(" .")
    if not package:
        legacy = _match_install_program(text, lower)
        return legacy
    skip = {"program", "uygulama", "yazilim", "yazılım", "paket", "bunu", "onu", "sunu", "şunu", "sunu"}
    if package in skip:
        return _match_install_program(text, lower)
    return LocalIntent(
        LocalToolRequest("install_program", {"package": package}),
        summary=f"Program kurulacak: {package}",
    )


def guess_file_action(
    message: str,
    *,
    resolved_references: dict[str, str] | None = None,
    conv_ctx: object | None = None,
) -> LocalIntent | None:
    """High-priority file ops — run locally without waiting for Hermes server."""
    text = (message or "").strip()
    if not text:
        return None

    from hermes.agent.task_planner import has_actionable_sequence, plan_local_sequence
    from hermes.mission.write_content import is_literal_composite_file_mission

    if is_literal_composite_file_mission(text):
        return None

    if has_actionable_sequence(text):
        if len(plan_local_sequence(text)) >= 2:
            return None

    lower = text.casefold()
    refs = dict(resolved_references or {})
    for matcher in (_match_word_document, _match_delete_path, _match_write_file):
        intent = matcher(text, lower, resolved_references=refs, conv_ctx=conv_ctx)
        if intent:
            return intent
    return None


def guess_local_action(
    message: str,
    *,
    resolved_references: dict[str, str] | None = None,
    conv_ctx: object | None = None,
) -> LocalIntent | None:
    """Route PC commands to local tools before asking Hermes."""
    from hermes.intent.turn_relation import has_negative_polarity

    if has_negative_polarity(message or ""):
        return None

    file_intent = guess_file_action(
        message,
        resolved_references=resolved_references,
        conv_ctx=conv_ctx,
    )
    if file_intent:
        return file_intent

    from hermes.agent.server_tasks import should_defer_to_server

    if should_defer_to_server(message):
        return None

    first = match_local_intent(message)
    if first:
        return first
    lower = (message or "").lower()
    if not lower.strip():
        return None

    from hermes.screen.reference import is_screen_perception_task

    if is_screen_perception_task(message):
        return None

    if _wants_screen_read(lower):
        return LocalIntent(LocalToolRequest("read_screen_text", {}), "Ekrandaki yazi okunacak.")
    if re.search(r"\bkontrol\b", lower) and re.search(r"ekran|pc|bilgisayar|sistem", lower):
        return LocalIntent(LocalToolRequest("read_screen_text", {}), "Ekrandaki yazi okunacak.")
    if re.search(r"\bdns\b", lower):
        if re.search(r"degistir|değiştir|ayarla|yap|kur", lower):
            return match_local_intent(message)
        return LocalIntent(LocalToolRequest("get_network_config", {}), "Ag bilgisi okunacak.")
    if extract_url_hint(message) and re.search(r"\b(ve|and|sonra|then)\b", lower):
        return None
    video = _match_video_intent(message, lower)
    if video:
        return video
    url = extract_url_hint(message) or _extract_youtube_url(message)
    if url and not _looks_like_local_filename(url):
        href = url if "://" in url else f"https://{url}"
        autoplay = "youtube" in href.lower() or "youtu.be" in href.lower()
        args: dict = {"url": href}
        if autoplay:
            args["autoplay"] = True
        return LocalIntent(LocalToolRequest("open_url", args), f"{href} acilacak.")
    folder = _match_create_folder(message, lower)
    if folder:
        return folder
    install = _match_install_program(message, lower)
    if install:
        return install
    from hermes.agent.application_catalog import resolve_application, resolve_web_url

    web_url = resolve_web_url(message)
    if web_url:
        return LocalIntent(
            LocalToolRequest("open_url", {"url": web_url}),
            summary=f"{web_url} acilacak.",
        )
    app = resolve_application(message) or _match_open_app(message) or _loose_app(lower)
    if app:
        return LocalIntent(LocalToolRequest("open_app", {"app": app}), f"{app} acilacak.")
    if re.search(r"sistem|bilgisayar|cpu|ram", lower):
        return LocalIntent(LocalToolRequest("get_system_info", {}), "Sistem bilgisi okunacak.")
    if re.search(r"disk", lower):
        return LocalIntent(LocalToolRequest("get_disk_info", {}), "Disk bilgisi okunacak.")
    if re.search(r"ag|ağ|wifi|dns|ip", lower):
        return LocalIntent(LocalToolRequest("get_network_config", {}), "Ag bilgisi okunacak.")
    interactive = _match_interactive_control(message, lower)
    if interactive:
        return interactive
    opened = _match_open_path(
        message,
        lower,
        conv_ctx=conv_ctx,
        resolved_references=resolved_references or {},
    )
    if opened:
        return opened
    return None


def _extract_click_target(text: str) -> str | None:
    patterns = (
        r"(?:tikla|tıkla|bas|basla|başla|sec|seç|ac|aç|izle|oynat|secip\s+ac)\s+(.+)$",
        r"(.+?)\s+(?:videosunu|video(?:yu|su)?)\s+(?:ac|aç|izle|oynat|tikla|tıkla|sec|seç)",
        r"(.+?)\s+(?:e\s+)?(?:tikla|tıkla)",
        r"(?:su|şu|o)\s+(.+?)\s+(?:ac|aç|izle|oynat)",
    )
    skip = {
        "video", "videoyu", "videosunu", "youtube", "chrome", "sayfa", "sayfayi", "sayfayı",
        "bunu", "onu", "sunu", "şunu", "lutfen", "abi", "akhi", "dostum",
    }
    for pattern in patterns:
        match = re.search(pattern, text.strip(), re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).strip(" .,'\"")
        words = [w for w in raw.split() if w.casefold() not in skip]
        target = " ".join(words).strip()
        if len(target) >= 2:
            return target
    return None


def _match_interactive_control(text: str, lower: str) -> LocalIntent | None:
    if extract_url_hint(text) or _extract_youtube_url(text):
        return None

    if re.search(
        r"masa[uü]st[uü]n[uü]?\s*(?:goster|göster|goster|git|ac|aç)|show\s*desktop|masa[uü]st[uü]ne\s*gec",
        lower,
    ):
        return LocalIntent(LocalToolRequest("show_desktop", {}), "Masaustu gosterilecek.")

    if re.search(
        r"sonuc|sonuç|ekranda\s*ne|ne\s*goruyorsun|ne\s*görüyorsun|ekrani\s*oku|ekranı\s*oku|"
        r"ekranda\s*ne\s*var|gorduklerini|gördüklerini",
        lower,
    ):
        return LocalIntent(LocalToolRequest("read_screen_text", {}), "Ekrandaki yazi okunacak.")

    if re.search(r"geri\s*(git|don|dön)|go\s*back|\bback\b", lower):
        return LocalIntent(LocalToolRequest("browser_nav", {"action": "back"}), "Geri gidiliyor.")

    if re.search(r"ileri\s*(git|gec|geç)|go\s*forward|\bforward\b", lower):
        return LocalIntent(LocalToolRequest("browser_nav", {"action": "forward"}), "Ileri gidiliyor.")

    if re.search(r"tam\s*ekran|buyuk\s*ekran|büyük\s*ekran|fullscreen|f11", lower):
        return LocalIntent(LocalToolRequest("browser_nav", {"action": "fullscreen"}), "Tam ekran yapiliyor.")

    if re.search(
        r"asagi|aşağı|asag[ıi]\s*in|scroll\s*down|page\s*down|kaydir|kaydır|"
        r"sayfayi\s*asagi|sayfayı\s*aşağı|biraz\s*asagi|biraz\s*aşağı|"
        r"yukari|yukarı|scroll\s*up|page\s*up",
        lower,
    ):
        from hermes.screen.scroll import parse_scroll_action

        action = parse_scroll_action(text)
        if action is not None:
            return LocalIntent(
                LocalToolRequest("scroll", action.to_tool_arguments()),
                "Asagi kaydiriliyor." if action.direction == "down" else "Yukari kaydiriliyor.",
            )

    if re.search(r"\b(tikla|tıkla|basla|başla|sec|seç)\b", lower) or re.search(
        r"videosunu\s+(ac|aç|izle|oynat)|video(?:yu|su)\s+(ac|aç|izle|oynat)", lower
    ):
        from hermes.screen.reference import is_literal_click_query

        target = _extract_click_target(text)
        if target and is_literal_click_query(text, target):
            return LocalIntent(
                LocalToolRequest("click_text", {"text": target, "partial": True}),
                f"Tiklanacak: {target}",
            )

    return None


def _extract_youtube_url(text: str) -> str | None:
    match = re.search(
        r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/watch\?[^\s]+|youtu\.be/[\w-]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    url = match.group(0)
    if not url.startswith("http"):
        url = f"https://{url.lstrip('/')}"
    return url


def _extract_youtube_search_query(text: str) -> str | None:
    """Tevhid ile arama yap gibi cumlelerden arama terimini cikar."""
    skip_words = {
        "hayir", "hayı", "tamam", "bro", "abi", "akhi", "dostum", "sadece", "simdi", "şimdi",
        "youtube", "youtubedan", "youtube'dan", "youtubeu", "youtube'u", "video", "videoyu",
        "bir", "tane", "ac", "aç", "izle", "oynat", "baslat", "başlat", "goster", "göster",
        "open", "ara", "aram", "yap", "ile", "ilgili", "hakkinda", "hakkında", "demek",
        "istiyorum", "brosur", "broşür", "browser", "tarayici", "tarayıcı", "lutfen", "please",
        "ve", "sonra", "icin", "için", "dan", "den", "de", "da",
    }
    patterns = (
        r"youtube(?:'?dan|'dan|da|'?u|'u)?\s+(?:.*?)([a-zA-ZçğıöşüÇĞİÖŞÜ0-9][\wçğıöşüÇĞİÖŞÜ0-9\s.-]{0,40}?)\s*(?:ile\s+)?(?:ilgili\s+)?(?:arama(?:ya|yı|yi)?|ara)",
        r"([a-zA-ZçğıöşüÇĞİÖŞÜ][\wçğıöşüÇĞİÖŞÜ0-9\s.-]{1,40}?)\s+ile\s+(?:ilgili\s+)?(?:bir\s+)?(?:video|arama)",
        r"(?:ara(?:ma)?(?:yı|yi|ya)?\s+)([a-zA-ZçğıöşüÇĞİÖŞÜ][\wçğıöşüÇĞİÖŞÜ0-9\s.-]{1,40})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        words = [
            w
            for w in match.group(1).strip(" .,'\"").split()
            if w.casefold() not in skip_words and len(w) > 1
        ]
        query = " ".join(words).strip()
        if len(query) >= 2:
            return query
    return None


def _match_video_intent(text: str, lower: str) -> LocalIntent | None:
    wants_video = bool(
        re.search(r"\b(video(?:yu|su|sunu)?|youtube|youtu\.be|izle|oynat)\b", lower)
        or ("youtube" in lower and re.search(r"\b(ara|aram|ac|aç)\b", lower))
    )
    if not wants_video:
        return None
    if not re.search(r"\b(ac|aç|open|izle|oynat|baslat|başlat|goster|göster|ara|aram)\b", lower):
        return None
    from hermes.screen.reference import is_screen_perception_task, looks_like_screen_reference

    if is_screen_perception_task(text) or looks_like_screen_reference(text):
        return None
    # Active screen / browser context: do not collapse to a YouTube search URL
    # when the user is selecting something already on screen.
    # (Folder alias stealing is handled separately below / in goal_parser.)
    url = _extract_youtube_url(text) or extract_url_hint(text)
    if url:
        href = url if "://" in url else f"https://{url}"
        return LocalIntent(
            LocalToolRequest("open_url", {"url": href, "autoplay": True}),
            summary=f"Video acilacak: {href}",
        )
    search_query = _extract_youtube_search_query(text)
    if search_query:
        href = f"https://www.youtube.com/results?search_query={quote(search_query)}"
        return LocalIntent(
            LocalToolRequest("open_url", {"url": href, "autoplay": True}),
            summary=f"YouTube aramasi acilacak: {search_query}",
        )
    query_match = re.search(
        r"(?:video(?:yu|yu)?|youtube(?:'?da|da)?)\s+(.+?)(?:\s+(?:ac|aç|izle|oynat)|$)",
        text,
        re.IGNORECASE,
    )
    if query_match:
        query = query_match.group(1).strip(" \"'")
        if query and len(query) > 2:
            href = f"https://www.youtube.com/results?search_query={quote(query)}"
            return LocalIntent(
                LocalToolRequest("open_url", {"url": href, "autoplay": True}),
                summary=f"YouTube aramasi acilacak: {query}",
            )
    return LocalIntent(
        LocalToolRequest("open_url", {"url": "https://www.youtube.com", "autoplay": True}),
        summary="YouTube acilacak.",
    )


def _match_create_folder(text: str, lower: str) -> LocalIntent | None:
    from hermes.context.folder_reference import (
        is_folder_create_message,
        is_folder_open_only_message,
        resolve_create_folder_path,
    )

    if is_folder_open_only_message(text):
        return None
    if not is_folder_create_message(text):
        return None

    folder_path = resolve_create_folder_path(text)
    if not folder_path:
        return None

    return LocalIntent(
        LocalToolRequest("create_folder", {"path": folder_path}),
        summary=f"Klasor olusturulacak: {folder_path}",
    )


def _match_open_path(
    text: str,
    lower: str,
    **kwargs: object,
) -> LocalIntent | None:
    from hermes.agent.application_catalog import (
        has_explicit_filename,
        is_web_or_app_open_message,
        resolve_application,
        resolve_web_url,
    )
    from hermes.screen.reference import (
        is_screen_perception_task,
        looks_like_media_open,
        looks_like_screen_reference,
    )

    if looks_like_media_open(text) or is_screen_perception_task(text) or looks_like_screen_reference(text):
        return None

    if is_web_or_app_open_message(text) and not has_explicit_filename(text):
        web_url = resolve_web_url(text)
        if web_url:
            return LocalIntent(
                LocalToolRequest("open_url", {"url": web_url}),
                summary=f"{web_url} acilacak.",
            )
        app = resolve_application(text)
        if app:
            return LocalIntent(
                LocalToolRequest("open_app", {"app": app}),
                summary=f"{app} acilacak.",
            )
        return None

    conv_ctx = kwargs.get("conv_ctx")
    resolved = kwargs.get("resolved_references") or {}
    if isinstance(resolved, dict) and resolved.get("target_file"):
        path = str(resolved["target_file"])
        if re.search(r"(?:ac|aç|open)", lower):
            return LocalIntent(
                LocalToolRequest("open_path", {"path": path}),
                summary=f"Acilacak: {path}",
            )
    if isinstance(resolved, dict) and resolved.get("target_folder"):
        path = str(resolved["target_folder"])
        if re.search(r"(?:ac|aç|open)", lower):
            return LocalIntent(
                LocalToolRequest("open_path", {"path": path}),
                summary=f"Klasor acilacak: {path}",
            )
    if conv_ctx is not None and re.search(
        r"klasor|klasör|folder|dizin", lower
    ) and re.search(r"(?:ac|aç|open)", lower):
        from hermes.context.goal_resolution import parse_open_folder_goal
        from hermes.context.reference_resolver import _normalize_existing_path

        explicit = parse_open_folder_goal(text)
        if explicit is not None:
            existing = _normalize_existing_path(str(explicit.path))
            if existing:
                return LocalIntent(
                    LocalToolRequest("open_path", {"path": str(existing)}),
                    summary=f"Klasor acilacak: {existing}",
                )
            return LocalIntent(
                LocalToolRequest("open_path", {"path": str(explicit.path)}),
                summary=f"Klasor acilacak: {explicit.path}",
            )

        folder = getattr(conv_ctx, "active_folder", None) or getattr(
            conv_ctx, "last_created_folder", None
        )
        if folder and not re.search(r"\b(olustur|oluştur|yaz|create)\b", lower):
            existing = _normalize_existing_path(str(folder))
            if existing:
                return LocalIntent(
                    LocalToolRequest("open_path", {"path": str(existing)}),
                    summary=f"Klasor acilacak: {existing}",
                )
    if conv_ctx is not None and re.search(r"dosya", lower) and re.search(
        r"(?:ac|aç|open)", lower
    ):
        if has_explicit_filename(text):
            explicit = re.search(r"\b([\w\d_.-]+\.(?:txt|md|docx|pdf))\b", text, re.IGNORECASE)
            if explicit:
                from hermes.context.reference_resolver import _normalize_existing_path

                file_name = explicit.group(1)
                for base in (
                    getattr(conv_ctx, "active_folder", None),
                    Path.home() / "Desktop",
                ):
                    if not base:
                        continue
                    candidate = Path(str(base)) / file_name
                    existing = _normalize_existing_path(str(candidate))
                    if existing:
                        return LocalIntent(
                            LocalToolRequest("open_path", {"path": str(existing)}),
                            summary=f"Acilacak: {existing}",
                        )
        active = getattr(conv_ctx, "active_file", None)
        if active and not re.search(r"\b(olustur|oluştur|yaz|create)\b", lower):
            return LocalIntent(
                LocalToolRequest("open_path", {"path": str(active)}),
                summary=f"Acilacak: {active}",
            )
    if not re.search(r"(?:ac|aç|open|baslat|başlat)\b", lower):
        return None
    if not re.search(r"klasor|klasör|folder|dizin|dosya", lower):
        return None
    from pathlib import Path

    desktop = Path.home() / "Desktop"
    full = re.search(r"([A-Za-z]:\\[^\s]+|/[^\s]+)", text)
    if full:
        path = full.group(1).strip()
        return LocalIntent(
            LocalToolRequest("open_path", {"path": path}),
            summary=f"Acilacak: {path}",
        )
    folder_match = re.search(
        r"(?:masa[uü]st(?:u|ü)(?:nde|de)?\s+)?([\w\s.-]+?)\s+klasor(?:u|yu|unu|ünü)?",
        text,
        re.IGNORECASE,
    )
    from hermes.context.goal_resolution import parse_open_folder_goal

    explicit = parse_open_folder_goal(text)
    if explicit is not None:
        path = str(explicit.path)
        return LocalIntent(
            LocalToolRequest("open_path", {"path": path}),
            summary=f"Klasor acilacak: {path}",
        )
    if folder_match:
        name = folder_match.group(1).strip(" .")
        path = str(desktop / name) if name else str(desktop)
        return LocalIntent(
            LocalToolRequest("open_path", {"path": path}),
            summary=f"Klasor acilacak: {path}",
        )
    name_match = re.search(r"\b([\w.-]+)\s*(?:klasor|klasör|folder)\b", lower)
    if name_match:
        path = str(desktop / name_match.group(1).strip())
        return LocalIntent(
            LocalToolRequest("open_path", {"path": path}),
            summary=f"Klasor acilacak: {path}",
        )
    return None


def _match_install_program(text: str, lower: str) -> LocalIntent | None:
    if not re.search(r"\b(kur|install|yukle|yükle|indir)\b", lower):
        return None
    if not re.search(r"\b(program|uygulama|yazilim|yazılım|paket)\b", lower):
        return None
    pkg_match = re.search(
        r"(?:program(?:lari|ları|i|ı)?|uygulama(?:lari|ları|yi|yı)?)\s+(.+?)(?:\s+kur|\s+install|$)",
        text,
        re.IGNORECASE,
    )
    package = (pkg_match.group(1).strip(" .") if pkg_match else "").strip()
    if not package:
        names = re.findall(r"\b(chrome|firefox|vscode|spotify|discord|7zip|git|node|python)\b", lower)
        package = names[0] if names else ""
    if not package:
        return None
    return LocalIntent(
        LocalToolRequest("install_program", {"package": package}),
        summary=f"Program kurulacak: {package}",
    )


def _wants_screen_read(lower: str) -> bool:
    return bool(
        re.search(
            r"ekran[ıi]?\s*oku|oku.*ekran|ekrana\s*bak|ekranda\s*ne|"
            r"ne\s*goruyorsun|ne\s*görüyorsun|ekrandaki|"
            r"\bocr\b|ekran\s*metn|screen\s*read|read\s*screen|"
            r"ekrani\s*oku|ekranı\s*oku",
            lower,
        )
    )


def _loose_app(lower: str) -> str | None:
    for name in ("chrome", "edge", "firefox", "notepad", "explorer", "spotify", "discord"):
        if name in lower and re.search(r"ac|aç|open|baslat|başlat", lower):
            return name
    return None


def _match_open_app(text: str) -> str | None:
    from hermes.agent.application_catalog import resolve_application

    return resolve_application(text)


def _extract_desktop_path(text: str, lower: str) -> str | None:
    from hermes.context.folder_reference import extract_desktop_relative_file_path

    return extract_desktop_relative_file_path(text)


def _word_content_for_message(text: str, lower: str, path: str) -> tuple[str, str]:
    if "tevhid" in lower:
        return "Tevhid", _TEVHID_CONTENT
    topic_match = re.search(
        r"(?:hakkında|hakkinda|ile ilgili|konusu|konulu)\s+(.+?)(?:\.|$|ve\s)",
        text,
        re.IGNORECASE,
    )
    topic = (topic_match.group(1).strip() if topic_match else Path(path).stem).strip(" .")
    title = topic.title() or "Belge"
    body = (
        f"{title} hakkinda ozet\n\n"
        f"Bu belge HERMES tarafindan {path} yoluna olusturulmustur.\n\n"
        f"Konu: {topic}\n\n"
        "Detayli icerik icin sunucu baglantisi gerekmeden yerel Word araci kullanildi."
    )
    return title, body


def _container_folder_from_context(
    conv_ctx: object | None,
    refs: dict[str, str],
) -> str | None:
    folder = refs.get("target_folder")
    if folder:
        return folder
    if conv_ctx is None:
        return None
    getter = getattr(conv_ctx, "focus_container", None)
    if callable(getter):
        folder = getter()
        if folder:
            return str(folder)
    focus = getattr(conv_ctx, "active_focus", None)
    if focus is not None and getattr(focus, "type", "") == "folder":
        return str(focus.identifier)
    return None


def _match_word_document(text: str, lower: str, **kwargs: object) -> LocalIntent | None:
    if not re.search(r"\b(word|docx|\.docx)\b", lower):
        return None
    if not re.search(
        r"\b(olustur|oluştur|yaz|hazirla|hazırla|doldur|create|ekle|yap)\b",
        lower,
    ):
        return None
    refs = dict(kwargs.get("resolved_references") or {}) if isinstance(kwargs.get("resolved_references"), dict) else {}
    conv_ctx = kwargs.get("conv_ctx")
    path = _extract_desktop_path(text, lower)
    if path and Path(path).stem.casefold() in {"içine", "icine", "word"}:
        path = None
    folder = _container_folder_from_context(conv_ctx if isinstance(conv_ctx, object) else None, refs)
    if folder:
        name = Path(path).name if path and str(path).lower().endswith(".docx") else None
        if not name:
            name = "tevhid.docx" if "tevhid" in lower else "belge.docx"
        path = str(Path(folder) / name)
    elif not path:
        path = "Hermes2/tevhid.docx"
    if not path.lower().endswith(".docx"):
        path = f"{path}.docx"
    title, content = _word_content_for_message(text, lower, path)
    return LocalIntent(
        LocalToolRequest(
            "create_word_document",
            {"path": path, "title": title, "content": content},
        ),
        summary=f"Word dosyasi olusturulacak: {path}",
    )


def _match_delete_path(text: str, lower: str, **kwargs: object) -> LocalIntent | None:
    if not re.search(r"\b(sil|delete|kaldir|kaldır|remove|temizle)\b", lower):
        return None
    path = _extract_desktop_path(text, lower)
    if not path:
        folder_match = re.search(
            r"\b(?:klasor|klasör|folder|dosya)\s+([\w\d_.-]+)\b",
            lower,
        )
        if folder_match:
            path = folder_match.group(1)
        else:
            for token in re.findall(r"\b(hermes\d*|[\w.-]+\.(?:docx|txt))\b", lower):
                if token not in {"word", "docx", "dosya", "klasor", "klasör"}:
                    path = token
                    break
    if not path:
        return None
    recursive = bool(
        re.search(r"\b(klasor|klasör|folder|recursive|tum|tüm|içindekiler|icindekiler)\b", lower)
        or ("/" not in path and "\\" not in path and "." not in path)
    )
    return LocalIntent(
        LocalToolRequest("delete_path", {"path": path, "recursive": recursive}),
        summary=f"Silinecek: {path}",
    )


def _match_write_file(
    text: str,
    lower: str,
    *,
    resolved_references: dict[str, str] | None = None,
    conv_ctx: object | None = None,
) -> LocalIntent | None:
    from hermes.context.content_extraction import extract_content_modification
    from hermes.mission.write_content import extract_literal_write_content, resolve_write_file_content

    refs = dict(resolved_references or {})
    hint = extract_literal_write_content(text)
    has_txt = bool(re.search(r"\b(txt|metin|text file|\.txt)\b", lower))
    has_write_verb = bool(re.search(r"\b(olustur|oluştur|yaz|create|degistir|değiştir|guncelle|güncelle)\b", lower))
    has_literal_write = bool(hint.literal_content and re.search(r"\b(yaz|write)\b", lower))
    has_inner_write = bool(re.search(r"içine|icerige|icerik", lower))
    content_only = bool(
        re.search(r"içeriğini|icerigini", lower)
        and not re.search(r"\b(olustur|oluştur|yarat|create)\b", lower)
    )

    if content_only:
        return None

    if not has_write_verb:
        return None
    if not (has_txt or has_literal_write or has_inner_write):
        return None

    from hermes.context.conversational_context import message_wants_container
    from hermes.context.folder_reference import message_uses_contextual_folder

    create_in_container = (
        (message_uses_contextual_folder(text) or message_wants_container(text))
        and bool(
            re.search(r"\b(word|docx|\.docx|olustur|oluştur|yarat|create)\b", lower)
            or _extract_filename_hint(text, lower)
        )
        and not re.search(r"dosyan[ıi]n\s+i[cç]ine|içeriğini|icerigini", lower)
    )

    path = _extract_desktop_path(text, lower)
    if create_in_container:
        folder = _container_folder_from_context(conv_ctx, refs)
        file_name = _extract_filename_hint(text, lower)
        if folder and file_name:
            path = str(Path(folder) / file_name)
        elif folder and path and Path(path).stem.casefold() in {"içine", "icine"}:
            path = None
        elif not file_name:
            path = None
    elif not path:
        focus = getattr(conv_ctx, "active_focus", None) if conv_ctx is not None else None
        if refs.get("target_file"):
            path = refs["target_file"]
        elif focus is not None and getattr(focus, "type", "") == "file":
            path = getattr(focus, "identifier", None)
        elif conv_ctx is not None and getattr(conv_ctx, "active_file", None):
            path = getattr(conv_ctx, "active_file")
        elif refs.get("target_folder"):
            file_name = _extract_filename_hint(text, lower)
            if file_name:
                path = str(Path(refs["target_folder"]) / file_name)
    if not path:
        return None
    if not path.lower().endswith(".txt"):
        from pathlib import Path as _Path

        path = f"{path}.txt" if "." not in _Path(path).name else path

    content = extract_literal_write_content(text).literal_content
    if content is None:
        content = extract_content_modification(text)
    if content is None:
        content = resolve_write_file_content(text)
    if content is None:
        return None
    from hermes.mission.write_content import is_placeholder_write_content

    if is_placeholder_write_content(content):
        return None
    return LocalIntent(
        LocalToolRequest("write_file", {"path": path, "content": content}),
        summary=f"Metin dosyasi yazilacak: {path}",
    )


def _extract_filename_hint(text: str, lower: str) -> str | None:
    match = re.search(r"\b([\w\d_.-]+\.(?:txt|md))\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"\b([\w\d_.-]+)\.txt\b", lower)
    if match:
        return match.group(0)
    return None


def summarize_local_result(intent: LocalIntent, result: object) -> str:
    success = bool(getattr(result, "success", False))
    error = getattr(result, "error", None)
    output = getattr(result, "output", None)
    name = intent.request.name
    if not success:
        err = str(error or "")
        if name == "open_path":
            return f"Acilamadi: {err or 'bilinmeyen hata'}"
        if "uac" in err.lower() or "yonetici" in err.lower() or "yönetici" in err.lower():
            return f"DNS ayarlanamadi: Windows yonetici izni (UAC) gerekli. UAC penceresini de onayla."
        return f"Islem yapilamadi: {error or 'bilinmeyen hata'}"
    if name == "set_dns" and isinstance(output, dict):
        servers = output.get("servers") or []
        adapter = output.get("adapter") or "ag karti"
        return f"{adapter} icin DNS guncellendi: {', '.join(str(s) for s in servers)}."
    if name == "screenshot":
        return "Ekran goruntusu alindi. Dosya yolunu sohbette gorebilirsin."
    if name == "read_screen_text" and isinstance(output, dict):
        text = str(output.get("text") or "").strip()
        lines = output.get("lines") or []
        if lines:
            preview = "\n".join(str(line) for line in lines[:12])
            return f"Ekranda gorunenler:\n{preview}"
        if text:
            return f"Ekranda gorunen yazi:\n{text[:2000]}"
        err = str(output.get("error") or "").strip()
        if err:
            return f"Ekran goruntusu alindi ama yazi okunamadi: {err}"
        return "Ekran goruntusu alindi. Yaziyi net okuyamadim; gorsel kaydedildi."
    if name == "list_windows" and isinstance(output, dict):
        windows = output.get("windows") or []
        preview = ", ".join(str(w) for w in windows[:8])
        return f"Acik pencereler ({len(windows)}): {preview or 'yok'}."
    if name == "get_network_config":
        return "Ag / DNS bilgisi okundu. Detay sohbette."
    if name == "get_system_info":
        return "Sistem bilgisi okundu. Detay sohbette."
    if name == "get_disk_info":
        return "Disk bilgisi okundu. Detay sohbette."
    if name == "open_app":
        from hermes.agent.user_messages import format_open_app_message

        return format_open_app_message(intent, result)
    if name == "open_url":
        return intent.summary.replace("acilacak", "acildi")
    if name == "create_folder" and isinstance(output, dict):
        from hermes.agent.user_messages import format_create_folder_message

        return format_create_folder_message(intent, result)
    if name == "open_path" and isinstance(output, dict):
        from hermes.agent.user_messages import format_open_path_message

        return format_open_path_message(intent, result)
    if name == "scroll":
        return "Sayfa kaydirildi."
    if name == "click_text" and isinstance(output, dict):
        return f"Tiklandi: {output.get('text') or output.get('match', intent.summary)}"
    if name == "show_desktop":
        return "Masaustu gosterildi."
    if name == "browser_nav" and isinstance(output, dict):
        action = str(output.get("action") or "").casefold()
        if action in {"back", "geri"}:
            return "Geri gidildi."
        if action in {"forward", "ileri"}:
            return "Ileri gidildi."
        if "fullscreen" in action or "tam" in action:
            return "Tam ekran yapildi."
        return "Tarayici komutu uygulandi."
    if name == "install_program" and isinstance(output, dict):
        note = output.get("note") or ""
        wid = output.get("winget_id") or output.get("package", "")
        verified = output.get("verified")
        base = f"Kurulum tamam: {wid or output.get('package', '')}"
        if note:
            base = f"{wid}: {note}"
        if verified is False:
            base += " (winget list ile dogrulama belirsiz — UAC onayini kontrol et)"
        return base
    if name == "install_program" and not success:
        err = str(error or "")
        if "uac" in err.casefold() or "zaman asim" in err.casefold() or "timeout" in err.casefold():
            return (
                f"Kurulum basarisiz: {err}\n"
                "Hermes onayindan sonra Windows UAC penceresini de onaylaman gerekir."
            )
        return f"Kurulum basarisiz: {err or 'bilinmeyen hata'}"
    if name == "set_volume":
        return "Ses ayari uygulandi."
    if name == "get_clipboard" and isinstance(output, dict):
        return f"Pano: {str(output.get('text') or '')[:400] or '(bos)'}"
    if name == "create_word_document" and isinstance(output, dict):
        listing = output.get("verified_listing") or []
        names = ", ".join(str(item.get("name")) for item in listing[:5])
        return (
            f"Word hazir: {output.get('path')} ({output.get('size', 0)} byte). "
            f"Klasor dogrulama: {names or 'ok'}"
        )
    if name == "write_file":
        if "guncellenecek" in (intent.summary or "").casefold():
            from hermes.agent.user_messages import format_modify_content_message

            return format_modify_content_message(intent, result)
        from hermes.agent.user_messages import format_write_file_message

        return format_write_file_message(intent, result)
    if name == "rename_path":
        from hermes.agent.user_messages import format_rename_path_message

        return format_rename_path_message(intent, result)
    if name == "delete_path":
        from hermes.agent.user_messages import format_delete_path_message

        return format_delete_path_message(intent, result)
    if name == "list_directory":
        from hermes.agent.user_messages import format_list_directory_message

        return format_list_directory_message(intent, result)
    if name == "read_file" and isinstance(output, dict):
        content = str(output.get("content") or "").strip()
        if content:
            return content if len(content) <= 4000 else content[:3997] + "..."
        return "Dosya okundu ancak icerik bos."
    return intent.summary.replace("olacak", "tamamlandi")
