from __future__ import annotations

from pathlib import Path

from hermes.agent.local_intent import LocalIntent


def _friendly_name(stem: str) -> str:
    text = (stem or "").strip()
    if not text:
        return "Dosya"
    return text[0].upper() + text[1:]


def format_file_create_message(intent: LocalIntent, result: object, *, kind: str = "create_file") -> str:
    success = bool(getattr(result, "success", False))
    output = getattr(result, "output", None) or {}
    if not success:
        return "Dosyayi olusturamadim."
    if isinstance(output, dict) and output.get("unchanged"):
        file_label = _friendly_name(Path(str(output.get("path") or "")).stem)
        return f"{file_label} dosyasi zaten ayni icerikteydi."
    if isinstance(output, dict) and output.get("verification_failed"):
        return "Dosya olusturma komutu gonderildi ancak dogrulayamadim."

    path_raw = ""
    if isinstance(output, dict):
        path_raw = str(output.get("path") or intent.request.arguments.get("path") or "")
    else:
        path_raw = str(intent.request.arguments.get("path") or "")

    try:
        path = resolve_display_path(path_raw)
    except (OSError, ValueError):
        return "Dosyayi olusturdum."

    file_label = _friendly_name(path.stem)
    content = str(intent.request.arguments.get("content") or "")
    if kind == "write_file" and content.strip():
        return f"{file_label} dosyasini olusturdum ve {content} yazdim."
    return f"{file_label} dosyasini olusturdum."


def format_write_file_message(intent: LocalIntent, result: object) -> str:
    return format_file_create_message(
        intent,
        result,
        kind="write_file" if str(intent.request.arguments.get("content") or "").strip() else "create_file",
    )


def format_open_path_message(intent: LocalIntent, result: object) -> str:
    success = bool(getattr(result, "success", False))
    output = getattr(result, "output", None) or {}
    error = getattr(result, "error", None)

    if not success:
        return f"Acilamadi: {error or 'bilinmeyen hata'}"

    if isinstance(output, dict):
        if output.get("reused"):
            path_str = str(output.get("path") or intent.request.arguments.get("path") or "")
            try:
                path = Path(path_str)
                is_folder = bool(output.get("is_directory")) or path.is_dir()
            except OSError:
                is_folder = bool(output.get("is_directory"))
            if is_folder:
                return "Klasor zaten acikti, one getirdim."
            return "Dosya zaten acikti, one getirdim."
        if output.get("verified") is True:
            path_str = str(output.get("path") or intent.request.arguments.get("path") or "")
            try:
                path = Path(path_str)
                is_folder = bool(output.get("is_directory")) or path.is_dir()
            except OSError:
                is_folder = False
            if is_folder:
                return "Klasoru actim."
            return "Dosyayi actim."
        note = str(output.get("verification_note") or "").strip()
        if note:
            return note
    return "Acma komutu gonderildi ancak dogrulayamadim."


def format_create_folder_message(intent: LocalIntent, result: object) -> str:
    success = bool(getattr(result, "success", False))
    output = getattr(result, "output", None) or {}
    if isinstance(output, dict) and output.get("verification_failed"):
        return "Klasoru olusturamadim."
    if not success:
        return "Klasoru olusturamadim."
    if isinstance(output, dict) and output.get("already_existed"):
        path_str = str(output.get("path") or intent.request.arguments.get("path") or "")
        name = Path(path_str).name if path_str else "klasor"
        return f"{name} klasoru zaten vardi."
    path_str = ""
    if isinstance(output, dict):
        path_str = str(output.get("path") or intent.request.arguments.get("path") or "")
    else:
        path_str = str(intent.request.arguments.get("path") or "")
    name = Path(path_str).name if path_str else "klasor"
    return f"{name} klasorunu masaustunde olusturdum."


def format_open_app_message(intent: LocalIntent, result: object) -> str:
    success = bool(getattr(result, "success", False))
    if not success:
        return "Uygulamayi acamadim."
    output = getattr(result, "output", None) or {}
    app = str(intent.request.arguments.get("app") or "uygulama")
    if isinstance(output, dict) and output.get("reused"):
        labels = {
            "chrome": "Chrome zaten acikti, one getirdim.",
            "edge": "Edge zaten acikti, one getirdim.",
            "firefox": "Firefox zaten acikti, one getirdim.",
            "notepad": "Not Defteri zaten acikti, one getirdim.",
            "explorer": "Gezgin zaten acikti, one getirdim.",
        }
        return labels.get(app.casefold(), f"{app.title()} zaten acikti, one getirdim.")
    labels = {
        "chrome": "Chrome'u actim.",
        "edge": "Edge'i actim.",
        "firefox": "Firefox'u actim.",
        "notepad": "Not Defteri'ni actim.",
        "explorer": "Gezgin'i actim.",
        "spotify": "Spotify'i actim.",
        "discord": "Discord'u actim.",
    }
    return labels.get(app.casefold(), f"{app.title()} uygulamasini actim.")


def format_modify_content_message(intent: LocalIntent, result: object) -> str:
    success = bool(getattr(result, "success", False))
    output = getattr(result, "output", None) or {}
    if not success or (isinstance(output, dict) and output.get("verification_failed")):
        return "Dosyanin icerigini degistiremedim."
    path_raw = str(
        (output.get("path") if isinstance(output, dict) else "")
        or intent.request.arguments.get("path")
        or ""
    )
    name = Path(path_raw).name if path_raw else "dosya"
    content = str(intent.request.arguments.get("content") or "").strip()
    if content:
        return f"{name} dosyasinin icerigini {content} olarak guncelledim."
    return f"{name} dosyasinin icerigini guncelledim."


def format_rename_path_message(intent: LocalIntent, result: object) -> str:
    success = bool(getattr(result, "success", False))
    output = getattr(result, "output", None) or {}
    if not success or (isinstance(output, dict) and output.get("verification_failed")):
        return "Dosya adini degistiremedim."
    if isinstance(output, dict):
        dest = str(output.get("destination") or output.get("path") or "")
        source = str(output.get("source") or intent.request.arguments.get("path") or "")
        if dest and source:
            return f"{Path(source).name} dosyasini {Path(dest).name} olarak yeniden adlandirdim."
    new_name = str(intent.request.arguments.get("new_name") or "")
    old_name = Path(str(intent.request.arguments.get("path") or "")).name
    if old_name and new_name:
        return f"{old_name} dosyasini {new_name} olarak yeniden adlandirdim."
    return "Dosya adini degistirdim."


def format_list_directory_message(intent: LocalIntent, result: object) -> str:
    success = bool(getattr(result, "success", False))
    if not success:
        return "Klasor icerigini okuyamadim."
    output = getattr(result, "output", None) or {}
    if not isinstance(output, dict):
        return "Klasor icerigini listeledim."
    count = int(output.get("count") or 0)
    path = Path(str(output.get("path") or intent.request.arguments.get("path") or ""))
    folder_name = path.name or "klasor"
    if count == 0:
        return f"{folder_name} klasoru bos."
    return f"{folder_name} klasorunde {count} oge var."


def format_delete_path_message(intent: LocalIntent, result: object) -> str:
    success = bool(getattr(result, "success", False))
    if not success:
        return "Dosyayi silemedim."
    output = getattr(result, "output", None) or {}
    if isinstance(output, dict) and output.get("exists_after") is True:
        return "Dosya silinemedi."
    path = Path(str(intent.request.arguments.get("path") or ""))
    return f"{path.stem} dosyasini sildim."


def resolve_display_path(raw: str) -> Path:
    text = (raw or "").strip()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path.home() / "Desktop" / path
    return path.resolve()
