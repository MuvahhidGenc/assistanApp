from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

_TOOL_NAMES = (
    "write_file",
    "create_file",
    "open_path",
    "create_folder",
    "list_windows",
    "list_directory",
    "delete_path",
    "open_app",
    "run_command",
    "execution_target",
    "tool_call",
    "verified",
    "verification",
)

_TECHNICAL_STATUS_MARKERS = (
    "anladim, isleme basliyorum",
    "referans cozuluyor",
    "referans netlestirme",
    "yerel tool",
    "tool calistir",
    "dogrulaniyor",
    "kontrol ediyorum",
    "tool sonucu",
    "execution_target",
    "planliyorum",
    "planlıyorum",
)

_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:\\[^\s,.;!?]+)|(?:/Users/[^\s,.;!?]+)|(?:/home/[^\s,.;!?]+)",
    re.IGNORECASE,
)

_APP_LABELS: dict[str, str] = {
    "chrome": "Chrome'u",
    "edge": "Edge'i",
    "firefox": "Firefox'u",
    "notepad": "Not Defteri'ni",
    "explorer": "Gezgin'i",
    "spotify": "Spotify'i",
    "discord": "Discord'u",
}


class TTSEvent(StrEnum):
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    NEEDS_CLARIFICATION = "needs_clarification"


def is_technical_status(text: str) -> bool:
    """True for orchestrator/UI progress lines that must never be spoken."""
    blob = (text or "").strip().casefold()
    if not blob:
        return True
    if any(marker in blob for marker in _TECHNICAL_STATUS_MARKERS):
        return True
    if any(tool in blob for tool in _TOOL_NAMES):
        return True
    if _PATH_PATTERN.search(text or ""):
        return True
    if "{" in blob or "}" in blob:
        return True
    return False


def sanitize_for_tts(text: str) -> str:
    """Remove paths, tool names and noisy tokens from candidate speech."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    cleaned = _PATH_PATTERN.sub("", cleaned)
    for tool in _TOOL_NAMES:
        cleaned = re.sub(re.escape(tool), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;")
    return cleaned


def _display_name(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        target = Path(raw).expanduser()
        if not target.is_absolute() and raw:
            target = Path.home() / "Desktop" / raw
        name = target.name
    except OSError:
        name = Path(raw).name
    if not name:
        return ""
    return name[0].upper() + name[1:]


def _is_folder_path(path: str) -> bool:
    raw = (path or "").strip()
    if not raw:
        return False
    try:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = Path.home() / "Desktop" / raw
        return target.is_dir() or not target.suffix
    except OSError:
        return not Path(raw).suffix


def _guess_tool_intent(user_message: str) -> tuple[str | None, dict[str, Any]]:
    from hermes.context.conversational_context import ConversationalContext
    from hermes.context.reference_resolver import ReferenceResolver

    ctx = ConversationalContext.load()
    resolution = ReferenceResolver().resolve(user_message, ctx)
    if resolution.intent:
        req = resolution.intent.request
        return str(req.name), dict(req.arguments or {})

    from hermes.agent.goal_router import GoalRouter
    from hermes.tools.registry import create_default_registry

    route = GoalRouter(create_default_registry()).route(user_message, ctx)
    if route.intent:
        return str(route.intent.request.name), dict(route.intent.request.arguments or {})

    from hermes.agent.local_intent import (
        guess_file_action,
        guess_install_action,
        guess_local_action,
    )

    for guesser in (guess_file_action, guess_install_action, guess_local_action):
        try:
            if guesser is guess_install_action:
                intent = guesser(user_message)
            else:
                intent = guesser(user_message, conv_ctx=ctx)
        except TypeError:
            intent = guesser(user_message)
        if intent:
            return str(intent.request.name), dict(intent.request.arguments or {})
    return None, {}


def _has_write_content(message: str) -> bool:
    from hermes.context.file_intent import has_explicit_write_content

    return has_explicit_write_content(message)


def _is_content_modify(message: str) -> bool:
    from hermes.context.file_intent import is_content_modify_only_message

    return is_content_modify_only_message(message)


def synthesize_task_started(user_message: str) -> str | None:
    """Short natural line when a user task begins (not from internal status)."""
    text = (user_message or "").strip()
    if not text:
        return None

    lower = text.casefold()

    if _is_content_modify(text):
        return "Dosyanın içeriğini değiştiriyorum."

    if re.search(r"\b(?:icine|içine)\b", lower) and re.search(
        r"\b(?:olustur|oluştur|yarat|create)\b", lower
    ):
        return "Dosyayı oluşturuyorum."

    tool, args = _guess_tool_intent(text)

    if tool == "open_path":
        if re.search(r"\bklas(?:o|ö)r", lower):
            return "Klasörü açıyorum."
        if re.search(r"\bdosya", lower):
            return "Dosyayı açıyorum."
        path = str(args.get("path") or "")
        if _is_folder_path(path):
            name = _display_name(path)
            if name and name.casefold() not in {"desktop", "masaustu", "masaüstü"}:
                return f"{name} klasörünü açıyorum."
            return "Klasörü açıyorum."
        return "Dosyayı açıyorum."

    if not tool:
        if re.search(r"\bklas(?:o|ö)r", lower) and re.search(
            r"\b(?:olustur|oluştur|yarat|create)\b", lower
        ):
            return "Klasörü oluşturuyorum."
        if re.search(r"\bklas(?:o|ö)r", lower) and re.search(r"\b(?:ac|aç|open)\b", lower):
            return "Klasörü açıyorum."
        if re.search(r"\bdosya", lower) and re.search(r"\b(?:ac|aç|open)\b", lower):
            return "Dosyayı açıyorum."
        if any(w in lower for w in ("ac ", "aç ", "open", "baslat", "başlat")):
            app = str(args.get("app") or "")
            if app:
                label = _APP_LABELS.get(app.casefold(), f"{app.title()}'u")
                return f"{label} açıyorum."
            return "Açıyorum."
        if any(w in lower for w in ("olustur", "oluştur", "yarat", "create")):
            return "Oluşturuyorum."
        if any(w in lower for w in ("sil", "delete", "kaldir", "kaldır")):
            return "Siliyorum."
        return None

    if tool == "create_folder":
        name = _display_name(str(args.get("path") or ""))
        if name:
            return f"{name} klasörünü oluşturuyorum."
        return "Klasörü oluşturuyorum."

    if tool == "open_app":
        app = str(args.get("app") or "").casefold()
        label = _APP_LABELS.get(app, f"{app.title()}'u" if app else "Uygulamayı")
        return f"{label} açıyorum."

    if tool in ("create_file", "write_file"):
        if _is_content_modify(text):
            return "Dosyanın içeriğini değiştiriyorum."
        if _has_write_content(text):
            return "Dosyayı oluşturuyorum."
        return "Dosyayı oluşturuyorum."

    if tool == "delete_path":
        if _is_folder_path(str(args.get("path") or "")):
            return "Klasörü siliyorum."
        return "Dosyayı siliyorum."

    if tool == "list_directory":
        return "Klasöre bakıyorum."

    if tool == "install_program":
        pkg = str(args.get("package") or args.get("name") or "").strip()
        if pkg:
            return f"{pkg} kurulumunu başlatıyorum."
        return "Kurulumu başlatıyorum."

    if tool == "set_dns":
        return "DNS ayarını değiştiriyorum."

    if tool == "read_screen_text":
        return "Ekrana bakıyorum."

    if tool == "screenshot":
        return "Ekran görüntüsü alıyorum."

    return None


_INCOMPLETE_OUTCOMES = frozenset(
    {"failed", "partial", "waiting", "question", "unsupported", "cancelled"}
)
_INCOMPLETE_MARKERS = (
    "kismen",
    "kısmen",
    "tamamlanamadi",
    "tamamlanamadı",
    "yapamiyorum",
    "yapamıyorum",
    "cozulemedi",
    "çözülemedi",
    "bekleniyor",
    "netlestir",
    "netleştir",
    "bilgiye ihtiyac",
    "zorunlu bir adim",
    "unsupported",
)
_SUCCESS_SPEECH = (
    "tamam, bitti",
    "tamam, hallettim",
    "gorev tamamlandi",
    "görev tamamlandı",
    "mission tamamlandi",
    "mission tamamlandı",
)


def _is_incomplete_outcome(outcome: str | None, text: str) -> bool:
    if (outcome or "").strip().casefold() in _INCOMPLETE_OUTCOMES:
        return True
    lower = (text or "").casefold()
    return any(marker in lower for marker in _INCOMPLETE_MARKERS)


def synthesize_task_completed(
    user_message: str,
    response_text: str,
    tool_results: list[dict[str, Any]] | None = None,
    *,
    outcome: str | None = None,
) -> str | None:
    """Natural completion/failure/clarification line from user-facing response text."""
    raw = (response_text or "").strip()
    if not raw:
        return None
    lower_raw = raw.casefold()

    path_folder = re.search(
        r"\\([^\\]+)\s+klasorunu\s+(?:masaustunde\s+)?olusturdum",
        lower_raw,
    )
    if path_folder:
        name = _display_name(path_folder.group(1))
        if name:
            return f"Tamam, {name} klasörünü oluşturdum."

    text = sanitize_for_tts(raw)
    if not text:
        return None
    lower = text.casefold()

    if is_technical_status(text):
        return None

    # Clarifications
    if "anlayamadim" in lower or "anlayamadım" in lower:
        if "hangi dosya" in lower or "dosyayi kasted" in lower:
            return "Hangi dosyayı kastettiğini anlayamadım."
        if "hangi klasor" in lower or "klasoru kasted" in lower:
            return "Hangi klasörü kastettiğini anlayamadım."
        if "icerik" in lower or "içerik" in lower:
            return "Ne yazmam gerektiğini anlayamadım."
        return "Tam anlayamadım, tekrar söyler misin?"

    if "netlestir" in lower or "netleştir" in lower or text.endswith("?"):
        short = text.rstrip(".")
        if len(short) <= 96 and not _PATH_PATTERN.search(response_text or ""):
            return short
        return "Biraz daha açar mısın?"

    # Verification failure — no fake success
    if "dogrulayamadim" in lower or "doğrulayamadım" in lower:
        if "acma" in lower or "açma" in lower or "actim" in lower or "açtım" in lower:
            return "Açma komutunu gönderdim ama açıldığını doğrulayamadım."
        return "İşlemi yaptım diyemem; sonucu doğrulayamadım."

    # Explicit failures
    if any(
        token in lower
        for token in (
            "olusturamadim",
            "oluşturamadım",
            "silemedim",
            "silinemedi",
            "acamadi",
            "açılamadı",
            "okuyamadim",
            "yapilamadi",
            "yapılamadı",
            "basarisiz",
            "başarısız",
        )
    ):
        tool, _ = _guess_tool_intent(user_message)
        if tool in ("create_file", "write_file"):
            return "Dosyayı oluşturamadım."
        if tool == "create_folder":
            return "Klasörü oluşturamadım."
        if tool == "open_path":
            return "Açamadım."
        if tool == "delete_path":
            return "Silemedim."
        return "Tamamlayamadım."

    if _is_incomplete_outcome(outcome, lower) or _is_incomplete_outcome(outcome, lower_raw):
        if text.endswith("?"):
            return text if len(text) <= 96 else "Biraz daha açar mısın?"
        if any(token in lower for token in ("basarisiz", "başarısız", "yapamad", "tamamlanamad")):
            return "İşi tamamlayamadım."
        first = text.split("\n", 1)[0].strip()
        folded_first = first.casefold()
        if (
            first
            and len(first) <= 96
            and not any(phrase in folded_first for phrase in _SUCCESS_SPEECH)
        ):
            return first.rstrip(".!")
        return "İşi tamamlayamadım."

    # Success — map from user_messages patterns
    folder_created = re.search(
        r"^(.+?)\s+klasorunu\s+(?:masaustunde\s+)?olusturdum",
        lower,
    )
    if folder_created:
        name = _display_name(folder_created.group(1)) or folder_created.group(1).strip()
        return f"Tamam, {name} klasörünü oluşturdum."

    if " dosyasini olusturdum ve " in lower or " dosyasını oluşturdum ve " in lower:
        if _is_content_modify(user_message):
            return "Tamam, içeriği değiştirdim."
        return "Tamam, oluşturdum ve yazdım."

    if " dosyasini olusturdum" in lower or " dosyasını oluşturdum" in lower:
        if _is_content_modify(user_message):
            return "Tamam, içeriği değiştirdim."
        return "Tamam, oluşturdum."

    if "klasoru actim" in lower or "klasörü açtım" in lower:
        return "Açtım."

    if "klasor zaten acikti" in lower or "klasör zaten açıktı" in lower:
        return "Klasör zaten açıktı."

    if "dosya zaten acikti" in lower or "dosya zaten açıktı" in lower:
        return "Dosya zaten açıktı."

    if "zaten acikti" in lower or "zaten açıktı" in lower:
        return "Zaten açıktı, öne getirdim."

    if "klasoru zaten vardi" in lower or "klasör zaten vardı" in lower:
        return "Klasör zaten vardı."

    if "dosyayi actim" in lower or "dosyayı açtım" in lower:
        return "Dosyayı açtım."

    for app, label in _APP_LABELS.items():
        if app in lower and ("actim" in lower or "açtım" in lower):
            return f"{label} açtım."

    if "not defteri" in lower and ("actim" in lower or "açtım" in lower):
        return "Not Defteri'ni açtım."

    if " actim" in lower or " açtım" in lower:
        return "Açtım."

    if " sildim" in lower or "silindi" in lower:
        return "Sildim."

    if "dns" in lower and any(w in lower for w in ("ayarlandi", "ayarlandı", "guncellendi", "güncellendi")):
        return "Tamam, DNS ayarlandı."

    if "klasoru bos" in lower or "klasör boş" in lower:
        return "Klasör boş."

    if " klasorunde " in lower and " oge var" in lower:
        return "Klasörde dosyalar var, detaylar sohbette."

    if "ekranda gorunen" in lower or ("ekran" in lower and "gorunen" in lower):
        return "Ekrana baktım, detaylar sohbette."

    if tool_results:
        last = tool_results[-1]
        name = str(last.get("name") or "")
        success = bool(last.get("success"))
        if not success:
            return synthesize_task_completed(user_message, "Basarisiz.", tool_results=None)

    if (outcome or "").strip().casefold() == "completed":
        done_words = ("tamam", "bitti", "hazir", "hazır", "tamamlandi", "tamamlandı")
        if any(word in lower for word in done_words):
            return "Tamam, bitti."
        return "Tamam, hallettim."

    # Conversational short replies
    if len(text) <= 72 and not any(marker in lower for marker in _TOOL_NAMES):
        first = text.split("\n", 1)[0].strip()
        if first and not _PATH_PATTERN.search(first):
            return first.rstrip(".!?")

    return None


def should_speak_event(event: TTSEvent, *, elapsed_ms: float = 0.0, start_delay_ms: float = 850.0) -> bool:
    """Whether a TASK_STARTED event should actually be spoken (slow tasks only)."""
    if event != TTSEvent.TASK_STARTED:
        return True
    return elapsed_ms >= start_delay_ms


def is_duplicate_speech(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    return previous.strip().casefold() == current.strip().casefold()
