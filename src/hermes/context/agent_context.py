"""Phase 8 — recency-aware conversational context resolution."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from hermes.context.conversational_context import ConversationalContext

IntentKind = Literal["open_file", "modify_file", "open_folder", "copy_target"]

_DEICTIC_FILE = (
    re.compile(r"\b(onu|bunu|şunu|sunu)\b", re.IGNORECASE),
    re.compile(r"\b(o|bu|şu|su)\s+dosya(?:y[ıi])?\b", re.IGNORECASE),
    re.compile(r"son\s+dosya", re.IGNORECASE),
    re.compile(r"son\s+olusturdugun\s+dosya", re.IGNORECASE),
    re.compile(r"son\s+oluşturduğun\s+dosya", re.IGNORECASE),
    re.compile(r"az\s+once(?:ki)?\s+(?:olusturdugun\s+)?dosya", re.IGNORECASE),
    re.compile(r"az\s+önce(?:ki)?\s+(?:oluşturduğun\s+)?dosya", re.IGNORECASE),
    re.compile(r"actigin\s+dosya|açtığın\s+dosya", re.IGNORECASE),
    re.compile(r"olusturdugumuz\s+dosya|oluşturduğumuz\s+dosya", re.IGNORECASE),
)

_SON_CREATED_FILE = re.compile(
    r"son\s+(?:olusturdugun|oluşturduğun|olusturdugumuz|oluşturduğumuz)\s+dosya",
    re.IGNORECASE,
)

_SON_OPENED_FILE = re.compile(
    r"son\s+(?:ac[ıi]lan|aç[ıi]lan|actigin|açtığın)\s+dosya",
    re.IGNORECASE,
)

_DEICTIC_FOLDER = (
    re.compile(r"\b(o|bu|şu|su)\s+klas(?:ö|o)r", re.IGNORECASE),
    re.compile(r"\b(oraya|buraya|orada|burada)\b", re.IGNORECASE),
    re.compile(r"olusturdugumuz\s+klas(?:o|ö)r|oluşturduğumuz\s+klas(?:ö|o)r", re.IGNORECASE),
    re.compile(r"son\s+olusturdugun\s+klas(?:o|ö)r", re.IGNORECASE),
    re.compile(r"son\s+oluşturduğun\s+klas(?:o|ö)r", re.IGNORECASE),
)

_ICINE_WRITE_ONLY = re.compile(
    r"^(?:icine|içine)\s+(.+?)\s+yaz\s*\.?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResolvedFile:
    path: str
    source: str


def path_exists(path: str | None, *, expect_file: bool = True) -> bool:
    if not path:
        return False
    try:
        target = Path(str(path))
        return target.is_file() if expect_file else target.is_dir()
    except OSError:
        return False


def normalize_existing_path(path: str | None) -> str | None:
    if not path:
        return None
    try:
        target = Path(str(path)).expanduser().resolve()
        return str(target) if target.exists() else None
    except OSError:
        return None


def is_deictic_file_reference(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _DEICTIC_FILE)


def is_deictic_folder_reference(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _DEICTIC_FOLDER)


def extract_icine_write_content(text: str) -> str | None:
    match = _ICINE_WRITE_ONLY.match((text or "").strip())
    if not match:
        return None
    content = match.group(1).strip(" .")
    if not content or re.search(r"\.txt\b|\.md\b", content, re.IGNORECASE):
        return None
    return content


def _verified_file_candidates(ctx: ConversationalContext) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(path: str | None, source: str, *, allow_missing: bool = False) -> None:
        if not path:
            return
        verified = normalize_existing_path(path)
        if not verified:
            if allow_missing:
                try:
                    verified = str(Path(str(path)).expanduser().resolve())
                except OSError:
                    verified = str(path)
            else:
                return
        key = verified.casefold()
        if key in seen:
            return
        seen.add(key)
        ordered.append((verified, source))

    add(getattr(ctx, "last_verified_file", None), "last_verified_file")
    add(ctx.last_created_file, "last_created_file")
    add(ctx.last_modified_file, "last_modified_file")
    add(ctx.last_renamed_file, "last_renamed_file", allow_missing=True)
    for index, path in enumerate(ctx.recent_files):
        add(path, f"recent_files[{index}]")
    for index, path in enumerate(ctx.created_files):
        add(path, f"created_files[{index}]")
    add(ctx.active_file, "active_file")
    add(ctx.last_opened_file, "last_opened_file")
    return ordered


def _verified_folder_candidates(ctx: ConversationalContext) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(path: str | None, source: str) -> None:
        if not path:
            return
        try:
            resolved = str(Path(str(path)).resolve())
        except OSError:
            resolved = str(path)
        if not path_exists(resolved, expect_file=False):
            return
        key = resolved.casefold()
        if key in seen:
            return
        seen.add(key)
        ordered.append((resolved, source))

    add(getattr(ctx, "last_verified_folder", None), "last_verified_folder")
    add(ctx.last_created_folder, "last_created_folder")
    add(ctx.active_folder, "active_folder")
    for index, path in enumerate(ctx.recent_folders):
        add(path, f"recent_folders[{index}]")
    add(ctx.last_opened_folder, "last_opened_folder")
    return ordered


def resolve_deictic_file(
    ctx: ConversationalContext,
    text: str = "",
    *,
    intent: IntentKind = "open_file",
) -> ResolvedFile | None:
    candidates = _verified_file_candidates(ctx)
    if not candidates:
        return None

    if _SON_CREATED_FILE.search(text or ""):
        for path, source in candidates:
            if "created" in source or "verified" in source or "modified" in source:
                return ResolvedFile(path=path, source=source)

    if _SON_OPENED_FILE.search(text or ""):
        for path, source in candidates:
            if source in {"last_opened_file", "last_verified_file"}:
                return ResolvedFile(path=path, source=source)

    skip_sources = {"last_opened_file"} if intent == "open_file" else set()
    for path, source in candidates:
        if source in skip_sources:
            continue
        return ResolvedFile(path=path, source=source)

    return ResolvedFile(path=candidates[0][0], source=candidates[0][1])


def resolve_deictic_folder(ctx: ConversationalContext, text: str = "") -> str | None:
    del text
    candidates = _verified_folder_candidates(ctx)
    return candidates[0][0] if candidates else None


def resolve_file_by_priority(
    ctx: ConversationalContext,
    *,
    prefer_modified: bool = False,
    text: str = "",
) -> ResolvedFile | None:
    intent: IntentKind = "modify_file" if prefer_modified else "open_file"
    if prefer_modified or (text.strip() and is_deictic_file_reference(text)):
        return resolve_deictic_file(ctx, text, intent=intent)

    candidates = _verified_file_candidates(ctx)
    if not candidates:
        return None
    if prefer_modified:
        for path, source in candidates:
            if source in {"last_modified_file", "last_verified_file", "last_created_file"}:
                return ResolvedFile(path=path, source=source)
    return ResolvedFile(path=candidates[0][0], source=candidates[0][1])


def resolve_folder_by_priority(ctx: ConversationalContext) -> str | None:
    candidates = _verified_folder_candidates(ctx)
    return candidates[0][0] if candidates else None


def resolve_context_file(
    text: str,
    ctx: ConversationalContext,
    *,
    prefer_modified: bool = False,
    allow_stem: bool = True,
) -> ResolvedFile | None:
    if is_deictic_file_reference(text) or prefer_modified:
        resolved = resolve_deictic_file(
            ctx,
            text,
            intent="modify_file" if prefer_modified else "open_file",
        )
        if resolved:
            return resolved
    if allow_stem:
        stem = resolve_file_from_message_stem(text, ctx)
        if stem:
            return stem
    return resolve_file_by_priority(ctx, prefer_modified=prefer_modified, text=text)


def resolve_context_folder(text: str, ctx: ConversationalContext) -> str | None:
    if is_deictic_folder_reference(text):
        return resolve_deictic_folder(ctx, text)
    return None


def resolve_file_from_message_stem(
    text: str,
    ctx: ConversationalContext,
) -> ResolvedFile | None:
    lower = (text or "").casefold()
    candidates: list[tuple[int, str, str]] = []

    for path, source in _verified_file_candidates(ctx):
        stem = Path(path).stem.casefold()
        if len(stem) >= 3 and stem in lower:
            candidates.append((len(stem), path, source))

    if len(candidates) == 1:
        return ResolvedFile(path=candidates[0][1], source=candidates[0][2])
    if len(candidates) > 1:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return ResolvedFile(path=candidates[0][1], source=candidates[0][2])
    return None


def find_folder_by_hint(ctx: ConversationalContext, hint: str) -> str | None:
    token = (hint or "").strip().casefold()
    if not token:
        return None
    for path, _source in _verified_folder_candidates(ctx):
        name = Path(path).name.casefold()
        if name == token or token in name:
            return path
    desktop = Path.home() / "Desktop" / hint
    if desktop.is_dir():
        return str(desktop.resolve())
    return None


def find_file_in_folder_hint(
    ctx: ConversationalContext,
    folder_hint: str,
    file_hint: str,
) -> str | None:
    folder_path = find_folder_by_hint(ctx, folder_hint)
    if not folder_path:
        return None
    file_name = Path(file_hint).name
    candidate = Path(folder_path) / file_name
    if candidate.is_file():
        return str(candidate.resolve())
    for path, _source in _verified_file_candidates(ctx):
        try:
            if Path(path).parent.resolve() == Path(folder_path).resolve():
                if Path(path).name.casefold() == file_name.casefold():
                    return path
        except OSError:
            continue
    return None


def promote_file_in_context(ctx: ConversationalContext, file_path: str) -> None:
    verified = normalize_existing_path(file_path)
    if not verified:
        return
    ctx.last_verified_file = verified
    ctx.active_file = verified
    ctx.last_created_file = verified
    ctx.last_modified_file = verified
    parent = str(Path(verified).parent)
    ctx.active_folder = parent
    ctx.last_verified_folder = parent
    ctx.recent_files = _dedupe_prepend(ctx.recent_files, verified)
    ctx.created_files = _dedupe_prepend(ctx.created_files, verified)
    ctx.commit_focus("file", verified, container=parent, source="promote")
    ctx.touch()


def promote_folder_in_context(ctx: ConversationalContext, folder_path: str) -> None:
    try:
        resolved = str(Path(str(folder_path)).resolve())
    except OSError:
        resolved = str(folder_path)
    if not path_exists(resolved, expect_file=False):
        return
    ctx.last_verified_folder = resolved
    ctx.active_folder = resolved
    ctx.last_created_folder = resolved
    ctx.recent_folders = _dedupe_prepend(ctx.recent_folders, resolved)
    ctx.commit_focus("folder", resolved, container=resolved, source="promote")
    ctx.touch()


def _dedupe_prepend(items: list[str], value: str, *, limit: int = 10) -> list[str]:
    filtered = [item for item in items if item.casefold() != value.casefold()]
    return [value, *filtered][:limit]


def action_record(
    action_type: str,
    summary: str,
    *,
    path: str | None = None,
    tool_name: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {"type": action_type, "summary": summary[:300]}
    if path:
        record["path"] = path
    if tool_name:
        record["tool"] = tool_name
    return record
