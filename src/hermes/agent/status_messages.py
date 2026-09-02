from __future__ import annotations

from pathlib import Path
from typing import Any


def format_executing_status(tool_name: str, arguments: dict[str, Any] | None = None) -> str:
    args = dict(arguments or {})
    name = (tool_name or "").strip()

    if name == "open_path":
        path = str(args.get("path") or "")
        try:
            target = Path(path)
            if target.is_dir() or not target.suffix:
                folder = target.name or "klasoru"
                from hermes.context.system_paths import folder_display_name

                return f"{folder_display_name(target)} klasorunu aciyorum..."
            return f"{target.stem} dosyasini aciyorum..."
        except OSError:
            return "Dosyayi aciyorum..."

    if name == "open_app":
        app = str(args.get("app") or "uygulama")
        labels = {
            "chrome": "Chrome'u",
            "edge": "Edge'i",
            "firefox": "Firefox'u",
            "notepad": "Not Defteri'ni",
            "explorer": "Gezgin'i",
            "spotify": "Spotify'i",
            "discord": "Discord'u",
        }
        return f"{labels.get(app.casefold(), app.title())} aciyorum..."

    mapping = {
        "write_file": "Dosyayi olusturuyorum...",
        "create_folder": "Klasoru olusturuyorum...",
        "delete_path": "Dosyayi siliyorum...",
        "list_directory": "Dosyalari listeliyorum...",
        "open_url": "Baglantiyi aciyorum...",
        "screenshot": "Ekran goruntusu aliyorum...",
        "read_screen_text": "Ekrani okuyorum...",
        "get_system_info": "Sistem bilgisini aliyorum...",
        "get_disk_info": "Disk bilgisini aliyorum...",
        "get_network_config": "Ag bilgisini aliyorum...",
        "set_volume": "Ses ayarini degistiriyorum...",
        "set_dns": "DNS ayarini degistiriyorum...",
        "install_program": "Programi kuruyorum...",
        "run_command": "Komutu calistiriyorum...",
        "get_clipboard": "Panoyu okuyorum...",
        "create_word_document": "Word belgesi olusturuyorum...",
        "search_files": "Dosyalari arıyorum...",
        "copy_file": "Dosyalari kopyaliyorum...",
        "rename_path": "Yeniden adlandiriyorum...",
        "read_screen_text": "Ekrandaki metni okuyorum...",
    }
    return mapping.get(name, "Islemi yapiyorum...")


def format_mission_planning_status() -> str:
    return "Gorevi planliyorum..."


def format_mission_step_status(title: str = "") -> str:
    label = (title or "").strip()
    if label:
        return f"{label}..."
    return "Siradaki adimi yapiyorum..."


def format_mission_recovery_status() -> str:
    return "Sorunu cozmeye calisiyorum..."


def format_mission_verifying_status() -> str:
    return "Kontrol ediyorum..."


def format_verifying_status(tool_name: str) -> str:
    return "Kontrol ediyorum..."


def format_planning_status() -> str:
    return "Planliyorum..."


def format_understanding_status() -> str:
    return "Anladim, isleme basliyorum..."


def format_completed_status() -> str:
    return "Tamam."
