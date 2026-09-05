from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hermes.agent.local_intent import LocalIntent, LocalToolRequest
from hermes.context.conversational_context import ConversationalContext
from hermes.context.system_paths import resolve_known_folder
from hermes.tools.registry import ToolRegistry

_KNOWN_APPS = (
    "chrome",
    "edge",
    "firefox",
    "notepad",
    "explorer",
    "spotify",
    "discord",
    "cmd",
    "powershell",
)

_OPEN_VERBS = re.compile(
    r"\b(ac|aç|open|baslat|başlat|calistir|çalıştır|gir|geç|gec)\b",
    re.IGNORECASE,
)
_LIST_VERBS = re.compile(
    r"\b(listele|goster|göster|list|goreb|göreb|icerig|içeriğ)\b",
    re.IGNORECASE,
)
_DELETE_VERBS = re.compile(r"\b(sil|delete|kaldir|kaldır|remove)\b", re.IGNORECASE)
_CREATE_VERBS = re.compile(r"\b(olustur|oluştur|yarat|create|hazirla|hazırla)\b", re.IGNORECASE)
_RENAME_VERBS = re.compile(
    r"\b(adini|adını|adlandir|adlandır|yeniden\s+ad|rename)\b",
    re.IGNORECASE,
)
_COPY_VERBS = re.compile(r"\b(kopyala|copy)\b", re.IGNORECASE)
_MOVE_VERBS = re.compile(r"\b(tasi|taşı|move|aktar)\b", re.IGNORECASE)
_VOLUME_VERBS = re.compile(r"\b(ses|volume|mute|sustur)\b", re.IGNORECASE)
_SCREENSHOT_VERBS = re.compile(r"\b(ekran\s*gorunt|screenshot|ss\s*al)\b", re.IGNORECASE)

_FILE_REF = re.compile(
    r"\b(onu|bunu|şunu|sunu|bu|o|şu|su)\s+dosya|"
    r"az\s+once(?:ki)?\s+dosya|az\s+önce(?:ki)?\s+dosya|"
    r"olusturdugumuz\s+dosya|oluşturduğumuz\s+dosya|"
    r"son\s+olusturdugumuz\s+dosya|son\s+oluşturduğumuz\s+dosya|"
    r"son\s+olusturdugun\s+dosya|son\s+oluşturduğun\s+dosya",
    re.IGNORECASE,
)
_FOLDER_REF = re.compile(
    r"\b(onu|bunu|şunu|sunu|bu|o|şu|su)\s+klas|"
    r"bu\s+klas|o\s+klas|ayni\s+klas|aynı\s+klas|"
    r"son\s+olusturdugun\s+klas|son\s+oluşturduğun\s+klas",
    re.IGNORECASE,
)


@dataclass
class ToolIntentResult:
    intent: LocalIntent | None = None
    ambiguous: bool = False
    clarification: str = ""
    resolved_references: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0


def _pick_file(ctx: ConversationalContext, refs: dict[str, str]) -> str | None:
    def _ok(path: str | None) -> str | None:
        if not path or ctx.is_invalidated(path):
            return None
        try:
            target = Path(str(path))
            return str(target.resolve()) if target.exists() else None
        except OSError:
            return None

    explicit = _ok(refs.get("target_file"))
    if explicit:
        return explicit
    focus = ctx.active_focus
    if focus is not None and focus.type == "file":
        chosen = _ok(focus.identifier)
        if chosen:
            return chosen
    for key in ("active_file", "last_created_file", "last_modified_file"):
        chosen = _ok(refs.get(key) or getattr(ctx, key, None))
        if chosen:
            return chosen
    for path in ctx.recent_files:
        chosen = _ok(path)
        if chosen:
            return chosen
    return None


def _pick_folder(ctx: ConversationalContext, refs: dict[str, str]) -> str | None:
    def _ok(path: str | None) -> str | None:
        if not path or ctx.is_invalidated(path):
            return None
        try:
            target = Path(str(path))
            return str(target.resolve()) if target.is_dir() else None
        except OSError:
            return None

    explicit = _ok(refs.get("target_folder"))
    if explicit:
        return explicit
    chosen = _ok(ctx.focus_container())
    if chosen:
        return chosen
    for key in ("active_folder", "last_created_folder"):
        chosen = _ok(refs.get(key) or getattr(ctx, key, None))
        if chosen:
            return chosen
    for path in ctx.recent_folders:
        chosen = _ok(path)
        if chosen:
            return chosen
    return None


def _extract_app_name(text: str, lower: str) -> str | None:
    for app in _KNOWN_APPS:
        if re.search(
            rf"\b{re.escape(app)}['']?(?:yu|yı|u|ü|ni|nı|yi|yı)?\b",
            lower,
        ):
            return app
        if app in lower and _OPEN_VERBS.search(lower):
            return app
    quoted = re.search(r"['\"]([^'\"]{2,40})['\"]", text)
    if quoted and _OPEN_VERBS.search(lower):
        return quoted.group(1).strip().casefold()
    program = re.search(
        r"(?:program(?:i|ı|u|ü)?|uygulama(?:y[ıi])?)\s+(.+?)\s+(?:ac|aç|calistir|çalıştır|baslat|başlat)",
        lower,
    )
    if program:
        return program.group(1).strip(" .'\"")
    loose = re.search(
        r"(?:su|şu|bu|o)\s+(.+?)\s+(?:program(?:i|ı|u|ü)?|uygulama(?:y[ıi])?)\s+(?:ac|aç|calistir|çalıştır)",
        lower,
    )
    if loose:
        return loose.group(1).strip(" .'\"")
    return None


def _extract_new_name(text: str, lower: str) -> str | None:
    patterns = (
        r"(?:adini|adını)\s+(.+?)\s+(?:yap|koy|ver|olsun)",
        r"(?:olarak|ad[ıi]yla)\s+(.+?)(?:\s+yap|\s+olsun|$)",
        r"(?:yeni\s+ad[ıi]\s+)?(.+?)\s+olsun",
    )
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            candidate = match.group(1).strip(" .'\"")
            if candidate and len(candidate) <= 120:
                return candidate
    return None


class ToolIntentMatcher:
    """Map natural language goals to client tools using registry metadata and context."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry

    def match(
        self,
        message: str,
        ctx: ConversationalContext,
        *,
        resolved_references: dict[str, str] | None = None,
    ) -> ToolIntentResult:
        text = (message or "").strip()
        if not text:
            return ToolIntentResult()
        lower = text.casefold()
        refs = dict(resolved_references or {})

        from hermes.agent.application_catalog import resolve_application, resolve_web_url
        from hermes.context.file_intent import is_file_read_message, resolve_read_file_path
        from hermes.context.folder_reference import (
            is_file_create_message,
            is_folder_create_message,
            is_folder_open_only_message,
            resolve_create_folder_path,
        )

        if is_folder_create_message(text) and not is_folder_open_only_message(text):
            if is_file_create_message(text):
                return ToolIntentResult()
            folder_path = resolve_create_folder_path(text)
            if folder_path:
                return ToolIntentResult(
                    intent=LocalIntent(
                        LocalToolRequest("create_folder", {"path": folder_path}),
                        f"Klasor olusturulacak: {folder_path}",
                    ),
                    resolved_references={"target_folder": folder_path},
                    confidence=0.93,
                )
            return ToolIntentResult(
                ambiguous=True,
                clarification="Hangi isimle klasor olusturmami istiyorsun?",
                confidence=0.5,
            )

        if is_file_read_message(text) and self._tool_exists("read_file"):
            file_path = resolve_read_file_path(text, ctx, resolved_references=refs)
            if file_path:
                return ToolIntentResult(
                    intent=LocalIntent(
                        LocalToolRequest("read_file", {"path": file_path}),
                        f"Dosya okunacak: {Path(file_path).name}",
                    ),
                    resolved_references={"target_file": file_path},
                    confidence=0.92,
                )
            return ToolIntentResult(
                ambiguous=True,
                clarification="Hangi dosyanin icerigini okumami istiyorsun?",
                confidence=0.5,
            )

        web_url = resolve_web_url(text)
        if web_url and self._tool_exists("open_url"):
            return ToolIntentResult(
                intent=LocalIntent(
                    LocalToolRequest("open_url", {"url": web_url}),
                    f"{web_url} acilacak",
                ),
                confidence=0.94,
            )

        app = resolve_application(text)
        if app and self._tool_exists("open_app"):
            return ToolIntentResult(
                intent=LocalIntent(
                    LocalToolRequest("open_app", {"app": app}),
                    f"{app} acilacak",
                ),
                resolved_references={"target_application": app},
                confidence=0.93,
            )

        if _SCREENSHOT_VERBS.search(lower):
            return ToolIntentResult(
                intent=LocalIntent(LocalToolRequest("screenshot", {}), "Ekran goruntusu"),
                confidence=0.9,
            )

        if _VOLUME_VERBS.search(lower):
            action = "mute"
            if re.search(r"\b(ac|aç|unmute)\b", lower):
                action = "unmute"
            elif re.search(r"\b(yukselt|yükselt|arttir|artır|up)\b", lower):
                action = "up"
            elif re.search(r"\b(kis|kıs|azalt|down)\b", lower):
                action = "down"
            return ToolIntentResult(
                intent=LocalIntent(
                    LocalToolRequest("set_volume", {"action": action}),
                    "Ses ayari",
                ),
                confidence=0.85,
            )

        if _DELETE_VERBS.search(lower) and (_FILE_REF.search(text) or refs.get("target_file")):
            path = _pick_file(ctx, refs)
            if not path:
                return ToolIntentResult(
                    ambiguous=True,
                    clarification="Hangi dosyayi silmemi istiyorsun?",
                )
            return ToolIntentResult(
                intent=LocalIntent(
                    LocalToolRequest("delete_path", {"path": path}),
                    "Dosya silinecek",
                ),
                resolved_references={"target_file": path},
                confidence=0.88,
            )

        if _RENAME_VERBS.search(lower):
            path = _pick_file(ctx, refs)
            if not path:
                return ToolIntentResult(
                    ambiguous=True,
                    clarification="Hangi dosyanin adini degistirmemi istiyorsun?",
                )
            new_name = _extract_new_name(text, lower)
            if not new_name:
                return ToolIntentResult(
                    ambiguous=True,
                    clarification="Dosyanin yeni adi ne olsun?",
                    resolved_references={"target_file": path},
                )
            target = Path(path).with_name(new_name)
            if self._tool_exists("rename_path"):
                return ToolIntentResult(
                    intent=LocalIntent(
                        LocalToolRequest("rename_path", {"path": path, "new_name": new_name}),
                        "Dosya adi degistirilecek",
                    ),
                    resolved_references={"target_file": str(target), "source_file": path},
                    confidence=0.88,
                )
            cmd = f'Rename-Item -LiteralPath "{path}" -NewName "{new_name}"'
            return ToolIntentResult(
                intent=LocalIntent(
                    LocalToolRequest("run_command", {"command": cmd, "shell": "powershell"}),
                    "Dosya adi degistirilecek",
                ),
                resolved_references={"target_file": str(target), "source_file": path},
                confidence=0.82,
            )

        if _LIST_VERBS.search(lower) and (
            _FOLDER_REF.search(text) or "klas" in lower or refs.get("target_folder")
        ):
            folder = _pick_folder(ctx, refs)
            if not folder:
                known = resolve_known_folder(text)
                if known and known.exists():
                    folder = str(known)
            if not folder:
                return ToolIntentResult(
                    ambiguous=True,
                    clarification="Hangi klasordeki dosyalari listelememi istiyorsun?",
                )
            return ToolIntentResult(
                intent=LocalIntent(
                    LocalToolRequest("list_directory", {"path": folder}),
                    "Klasor listelenecek",
                ),
                resolved_references={"target_folder": folder},
                confidence=0.86,
            )

        if _OPEN_VERBS.search(lower):
            from hermes.agent.application_catalog import has_explicit_filename, is_web_or_app_open_message

            if is_web_or_app_open_message(text) and not has_explicit_filename(text):
                web_url = resolve_web_url(text)
                if web_url and self._tool_exists("open_url"):
                    return ToolIntentResult(
                        intent=LocalIntent(
                            LocalToolRequest("open_url", {"url": web_url}),
                            f"{web_url} acilacak",
                        ),
                        confidence=0.94,
                    )
                app = resolve_application(text)
                if app and self._tool_exists("open_app"):
                    return ToolIntentResult(
                        intent=LocalIntent(
                            LocalToolRequest("open_app", {"app": app}),
                            f"{app} acilacak",
                        ),
                        resolved_references={"target_application": app},
                        confidence=0.93,
                    )

            known = resolve_known_folder(text)
            if known and known.exists():
                folder = str(known)
                return ToolIntentResult(
                    intent=LocalIntent(
                        LocalToolRequest("open_path", {"path": folder}),
                        "Klasor acilacak",
                    ),
                    resolved_references={"target_folder": folder},
                    confidence=0.88,
                )

            if _FOLDER_REF.search(text) or (
                re.search(r"\bklas(?:o|ö)r", lower) and not _FILE_REF.search(text)
            ):
                folder = _pick_folder(ctx, refs)
                if folder:
                    return ToolIntentResult(
                        intent=LocalIntent(
                            LocalToolRequest("open_path", {"path": folder}),
                            "Klasor acilacak",
                        ),
                        resolved_references={"target_folder": folder},
                        confidence=0.87,
                    )

            if _FILE_REF.search(text):
                path = _pick_file(ctx, refs)
                if path:
                    return ToolIntentResult(
                        intent=LocalIntent(
                            LocalToolRequest("open_path", {"path": path}),
                            "Dosya acilacak",
                        ),
                        resolved_references={"target_file": path},
                        confidence=0.87,
                    )
                return ToolIntentResult(
                    ambiguous=True,
                    clarification="Hangi dosyayi acmami istiyorsun?",
                )

        if _COPY_VERBS.search(lower) or _MOVE_VERBS.search(lower):
            source = _pick_file(ctx, refs)
            if not source:
                return ToolIntentResult(
                    ambiguous=True,
                    clarification="Hangi dosyayi tasimam/kopyalamam gerekiyor?",
                )
            dest_match = re.search(
                r"(?:masaust|masaüst|belgeler|documents|indirilen|downloads|klas(?:o|ö)r(?:e|a|une|üne))\S*",
                lower,
            )
            if not dest_match:
                return ToolIntentResult(
                    ambiguous=True,
                    clarification="Dosyayi nereye tasimam/kopyalamam gerekiyor?",
                    resolved_references={"target_file": source},
                )
            known = resolve_known_folder(dest_match.group(0))
            if not known:
                return ToolIntentResult(
                    ambiguous=True,
                    clarification="Hedef klasoru tam olarak soyleyebilir misin?",
                    resolved_references={"target_file": source},
                )
            dest = known / Path(source).name
            verb = "copy_file" if _COPY_VERBS.search(lower) else "move_file"
            if self._tool_exists(verb):
                return ToolIntentResult(
                    intent=LocalIntent(
                        LocalToolRequest(verb, {"source": source, "destination": str(dest)}),
                        "Dosya islemi",
                    ),
                    resolved_references={"target_file": str(dest), "source_file": source},
                    confidence=0.86,
                )
            cmd_verb = "Copy-Item" if _COPY_VERBS.search(lower) else "Move-Item"
            cmd = f'{cmd_verb} -LiteralPath "{source}" -Destination "{dest}" -Force'
            return ToolIntentResult(
                intent=LocalIntent(
                    LocalToolRequest("run_command", {"command": cmd, "shell": "powershell"}),
                    "Dosya islemi",
                ),
                resolved_references={"target_file": str(dest), "source_file": source},
                confidence=0.8,
            )

        if _CREATE_VERBS.search(lower) and re.search(r"\bklas(?:o|ö)r\b", lower):
            from hermes.agent.local_intent import _match_create_folder

            intent = _match_create_folder(text, lower)
            if intent:
                return ToolIntentResult(intent=intent, confidence=0.84)

        return ToolIntentResult()

    def _tool_exists(self, name: str) -> bool:
        if self._registry is None:
            return True
        return self._registry.get(name) is not None


def match_tool_intent(
    message: str,
    ctx: ConversationalContext,
    *,
    registry: ToolRegistry | None = None,
    resolved_references: dict[str, str] | None = None,
) -> ToolIntentResult:
    return ToolIntentMatcher(registry).match(
        message, ctx, resolved_references=resolved_references
    )
