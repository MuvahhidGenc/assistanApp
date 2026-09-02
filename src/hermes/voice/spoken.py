from __future__ import annotations

import re
from typing import Any

_TOOL_LINE = re.compile(r"LOCAL_TOOL\s*\{.*?\}", re.IGNORECASE | re.DOTALL)
_MARKDOWN = re.compile(r"[*_`#>\[\]()]+")
_MAX_SPOKEN = 96

_CONVERSATIONAL = (
    "anladım",
    "anladim",
    "tamam",
    "tabii",
    "tabi",
    "elbette",
    "peki",
    "olur",
    "hemen",
    "hazırım",
    "hazirim",
    "işlem",
    "islem",
    "yapıyorum",
    "yapiyorum",
    "bakıyorum",
    "bakiyorum",
    "açıyorum",
    "aciyorum",
    "kuruyorum",
    "ayarlıyorum",
    "ayarliyorum",
    "bakarim",
    "bakarım",
    "hallediyorum",
    "devam",
    "bir saniye",
    "hemen bak",
    "ekliyorum",
    "olusturuyorum",
    "oluşturuyorum",
    "siliyorum",
    "indiriyorum",
    "kopyaliyorum",
    "kopyalıyorum",
)

_DATA_MARKERS = (
    "local_tool",
    "tool_result",
    "http://",
    "https://",
    "{",
    "}",
    "8.8.8.8",
    "1.1.1.1",
    "cpu",
    "ram",
    "windows",
    "adapter",
    "winget_id",
    "json",
)


def _clean_text(text: str) -> str:
    cleaned = _TOOL_LINE.sub("", text or "")
    cleaned = _MARKDOWN.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _first_sentence(text: str) -> str:
    line = text.split("\n", 1)[0].strip()
    match = re.match(r"^(.+?[.!?])(?:\s|$)", line)
    if match and len(match.group(1)) <= _MAX_SPOKEN:
        return match.group(1).strip()
    return line


def _is_conversational(sentence: str) -> bool:
    lower = sentence.casefold()
    if any(marker in lower for marker in _DATA_MARKERS):
        return False
    return any(token in lower for token in _CONVERSATIONAL)


def _arg(intent: Any, key: str, default: str = "") -> str:
    if intent is None:
        return default
    args = getattr(getattr(intent, "request", None), "arguments", None) or {}
    return str(args.get(key) or default).strip()


def spoken_line_for_tool(tool_name: str, intent: Any = None) -> str:
    pkg = _arg(intent, "package") or _arg(intent, "name")
    app = _arg(intent, "app")
    path = _arg(intent, "path")
    url = _arg(intent, "url") or _arg(intent, "link")

    lines = {
        "set_dns": "Tamam, DNS ayarlıyorum.",
        "open_url": "Tamam, linki açıyorum." if not url else "Tamam, sayfayı açıyorum.",
        "open_app": f"Tamam, {app} açıyorum." if app else "Tamam, uygulamayı açıyorum.",
        "open_path": f"Tamam, {path or 'klasörü'} açıyorum." if path else "Tamam, açıyorum.",
        "create_folder": f"Tamam, {path or 'klasörü'} oluşturuyorum.",
        "install_program": f"Tamam, {pkg} kurulumunu başlatıyorum." if pkg else "Tamam, kurulumu başlatıyorum.",
        "read_screen_text": "Ekrana bakıyorum, ne var bakayım.",
        "screenshot": "Ekran görüntüsü alıyorum.",
        "get_network_config": "Ağ bilgisine bakıyorum.",
        "get_system_info": "Sistem bilgisine bakıyorum.",
        "create_word_document": "Tamam, Word belgesini hazırlıyorum.",
        "write_file": "Tamam, dosyayı yazıyorum.",
        "delete_path": "Tamam, siliyorum.",
        "download_file": "Tamam, indiriyorum.",
        "git_clone": "Tamam, repoyu klonluyorum.",
        "list_directory": "Tamam, klasöre bakıyorum.",
        "run_command": "Tamam, komutu çalıştırıyorum.",
        "focus_window": "Tamam, pencereye geçiyorum.",
        "type_text": "Tamam, yazıyorum.",
        "click": "Tamam, tıklıyorum.",
        "click_text": "Tamam, ekranda bulup tıklıyorum.",
        "scroll": "Tamam, kaydırıyorum.",
        "show_desktop": "Masaüstünü gösteriyorum.",
        "browser_nav": "Tamam, tarayıcıda yapıyorum.",
        "press_keys": "Tamam, kısayolu gönderiyorum.",
        "move_mouse": "Fareyi hareket ettiriyorum.",
    }
    return lines.get(tool_name, "Anladım, hallediyorum.")


def spoken_quick_ack(user_message: str) -> str:
    from hermes.voice.response_synthesizer import synthesize_task_started

    line = synthesize_task_started(user_message)
    if line:
        return line.rstrip(".!?")
    return "Tamam, bakıyorum."


def brief_status_line(status: str) -> str:
    blob = (status or "").casefold()
    if "onay" in blob:
        return "Onayını bekliyorum abi."
    if "duydum" in blob:
        return ""
    if "komutunu dinliyorum" in blob or "dinliyorum" in blob:
        return "Dinliyorum abi."
    if "anlayamad" in blob or "anlasilamadi" in blob:
        return "Anlayamadım, tekrar söyle."
    if "dns" in blob:
        return "DNS ayarlıyorum."
    if "kurulum" in blob or "install" in blob or "winget" in blob:
        return "Kurulum devam ediyor, biraz sürebilir."
    if "word" in blob or "belge" in blob or "docx" in blob:
        return "Belgeyi hazırlıyorum."
    if "indir" in blob or "download" in blob:
        return "İndiriyorum."
    if "video" in blob or "url" in blob or "youtube" in blob:
        return "Videoyu açıyorum."
    if "ekran" in blob or "ocr" in blob:
        return "Ekrana bakıyorum."
    if "calistir" in blob or "execut" in blob or "tool" in blob or "adim" in blob:
        return "İşlem yapıyorum."
    if "plan" in blob:
        return "Planlıyorum."
    if "anlas" in blob or "understand" in blob:
        return "Anladım."
    return ""


def brief_spoken_reply(text: str, *, user_message: str = "") -> str:
    """Short conversational voice line; full answer stays in chat."""
    from hermes.voice.response_synthesizer import synthesize_task_completed

    synthesized = synthesize_task_completed(user_message, text)
    if synthesized:
        first = _first_sentence(synthesized)
        return first.rstrip(".!?")
    cleaned = _clean_text(text)
    if not cleaned:
        return "Buradayım abi."

    first = _first_sentence(cleaned)
    if first and len(first) <= _MAX_SPOKEN and _is_conversational(first):
        return first.rstrip(".!?")

    if len(cleaned) <= 64 and not any(marker in cleaned.casefold() for marker in _DATA_MARKERS):
        return cleaned.rstrip(".!?")

    if len(cleaned) <= 62 and _is_conversational(cleaned):
        return cleaned.rstrip(".!?")

    lower = cleaned.casefold()
    if any(w in lower for w in ("hata", "basarisiz", "başarısız", "yapilamadi", "yapılamadı", "redded", "uac")):
        if "uac" in lower or "yonetici" in lower or "yönetici" in lower:
            return "Windows yönetici izni lazım abi, UAC penceresini onayla."
        if "zaman asim" in lower or "timeout" in lower:
            return "Zaman aşımı oldu, sohbette detay var."
        return "Tamamlayamadım abi, detaylar sohbette."
    if any(w in lower for w in ("dns guncellendi", "dns güncellendi", "dns ayarlandi", "dns ayarlandı")):
        return "Tamam, DNS ayarlandı."
    if any(w in lower for w in ("kurulum tamam", "kuruldu", "zaten kurulu", "install complete", "successfully installed")):
        return "Kurulum bitti abi."
    if any(w in lower for w in ("kurulum", "install", "winget")) and any(
        w in lower for w in ("basladi", "başladı", "baslat", "başlat")
    ):
        return "Kurulumu başlattım, biraz sürebilir."
    if any(w in lower for w in ("olusturuldu", "oluşturuldu", "hazir", "hazır")) and any(
        w in lower for w in ("word", "docx", "belge", "dosya")
    ):
        return "Belge hazır abi, sohbette yolu var."
    if any(w in lower for w in ("silindi", "sildim", "kaldirildi", "kaldırıldı", "deleted")):
        return "Sildim abi."
    if any(w in lower for w in ("indirildi", "indirdim", "download")) and "bytes" not in lower:
        return "İndirdim abi."
    if any(w in lower for w in ("klon", "clone", "git")) and any(w in lower for w in ("tamam", "bitti", "ok")):
        return "Repoyu klonladım."
    if any(w in lower for w in ("video", "youtube", "acildi", "açıldı", "oynat")):
        return "Tamam, videoyu açtım."
    if any(w in lower for w in ("ekran", "ocr", "screenshot", "gorunen", "görünen")):
        return "Ekrana baktım abi, detaylar sohbette."
    if any(w in lower for w in ("dns", "ag ", "ağ", "wifi", "network", "ip ")):
        return "Ağ işlemi bitti abi."
    if any(w in lower for w in ("acildi", "açıldı", "acilacak", "chrome", "edge", "firefox", "notepad", "spotify")):
        return "Uygulamayı açtım abi."
    if any(w in lower for w in ("klasor", "klasör", "olusturuldu", "oluşturuldu")):
        return "Klasör hazır abi."
    if any(w in lower for w in ("list_directory", "klasor icerigi", "klasör içeriği", "dosya list")):
        return "Klasöre baktım, liste sohbette."
    if any(w in lower for w in ("tamamlandi", "tamamlandı", "bitti", "hazir", "hazır", "done")):
        return "Tamam abi, bitti. Detaylar sohbette."
    return "Tamam abi, hallettim. Detaylar sohbette."


def looks_like_missing_tools(text: str) -> bool:
    blob = (text or "").casefold()
    needles = (
        "local tool",
        "local tools",
        "yerel araç",
        "yerel arac",
        "yerel tool",
        "arac yok",
        "araç yok",
        "tool yok",
        "bulunamadi",
        "bulunamadı",
        "bulunmuyor",
        "yapamiyorum",
        "yapamıyorum",
        "yapamam",
        "erişimim yok",
        "erisimim yok",
        "erişemiyorum",
        "erisemiyorum",
        "i don't have",
        "i do not have",
        "don't have access",
        "cannot access",
        "can't access",
        "unable to",
        "no local",
        "not available on",
        "this environment",
        "on the server",
        "sunucuda çalış",
        "sunucuda calis",
        "server-side",
        "don't have the ability",
    )
    return any(needle in blob for needle in needles)


def sanitize_assistant_text(text: str) -> str:
    """Remove 'no local tools' excuses from chat when we will run locally."""
    if not looks_like_missing_tools(text):
        return text
    return ""
