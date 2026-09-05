from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pathlib import Path

from hermes.context.conversational_context import ConversationalContext
from hermes.context.content_extraction import extract_content_modification
from hermes.context.folder_reference import (
    extract_named_folder_name,
    extract_new_filename,
    message_uses_contextual_folder,
    resolve_folder_path_from_message,
)
from hermes.mission.write_content import extract_literal_write_content

_FILE_REF_PATTERNS = (
    re.compile(r"\b(onu|bunu|şunu|sunu)\b", re.IGNORECASE),
    re.compile(r"\b(o|bu|şu|su)\s+dosya(?:y[ıi])?\b", re.IGNORECASE),
    re.compile(r"az\s+önce(?:ki)?\s+dosya", re.IGNORECASE),
    re.compile(r"az\s+once(?:ki)?\s+dosya", re.IGNORECASE),
    re.compile(r"önceki\s+dosya", re.IGNORECASE),
    re.compile(r"onceki\s+dosya", re.IGNORECASE),
    re.compile(r"oluşturduğumuz\s+dosya(?:y[ıi])?", re.IGNORECASE),
    re.compile(r"olusturdugumuz\s+dosya(?:yi|yi)?", re.IGNORECASE),
    re.compile(r"son\s+oluşturduğun\s+dosya(?:y[ıi])?", re.IGNORECASE),
    re.compile(r"son\s+olusturdugun\s+dosya(?:yi|yi)?", re.IGNORECASE),
    re.compile(r"son\s+oluşturduğumuz\s+dosya(?:y[ıi])?", re.IGNORECASE),
    re.compile(r"son\s+olusturdugumuz\s+dosya(?:yi|yi)?", re.IGNORECASE),
    re.compile(r"aynı\s+dosya(?:ya|yı)?", re.IGNORECASE),
    re.compile(r"ayni\s+dosya(?:ya|yi)?", re.IGNORECASE),
)

_FOLDER_REF_PATTERNS = (
    re.compile(r"\b(o|bu|şu|su)\s+klas(?:ö|o)r(?:u|ü|e|a|une|üne|re|ye)?\b", re.IGNORECASE),
    re.compile(r"\b(oraya|buraya)\b", re.IGNORECASE),
    re.compile(r"aynı\s+klas(?:ö|o)re", re.IGNORECASE),
    re.compile(r"ayni\s+klas(?:o|ö)re", re.IGNORECASE),
    re.compile(r"son\s+oluşturduğun\s+klas(?:ö|o)r(?:u|ü|unun|ünün)?", re.IGNORECASE),
    re.compile(r"son\s+olusturdugun\s+klas(?:o|ö)r(?:u|u|unun|unun)?", re.IGNORECASE),
    re.compile(r"son\s+oluşturduğun\s+klas(?:ö|o)re", re.IGNORECASE),
    re.compile(r"son\s+olusturdugun\s+klas(?:o|ö)re", re.IGNORECASE),
    re.compile(r"oluşturduğumuz\s+klas(?:ö|o)r(?:u|ü|unu|ünü)?", re.IGNORECASE),
    re.compile(r"olusturdugumuz\s+klas(?:o|ö)r(?:u|u|unun|unun)?", re.IGNORECASE),
)

_FOLDER_OPEN_PATTERNS = _FOLDER_REF_PATTERNS + (
    re.compile(r"\bklas(?:ö|o)r(?:u|ü|unu|ünü)\b", re.IGNORECASE),
)

_CONTENT_MODIFY_PATTERNS = (
    re.compile(r"içeriğini", re.IGNORECASE),
    re.compile(r"icerigini", re.IGNORECASE),
    re.compile(r"içeriğini\s+değiştir", re.IGNORECASE),
    re.compile(r"icerigini\s+degistir", re.IGNORECASE),
    re.compile(r"dosyanın\s+içeriğini", re.IGNORECASE),
    re.compile(r"dosyanin\s+icerigini", re.IGNORECASE),
)

_RENAME_PATTERNS = (
    re.compile(r"\badini\b", re.IGNORECASE),
    re.compile(r"\badını\b", re.IGNORECASE),
    re.compile(r"\byeniden\s+ad", re.IGNORECASE),
    re.compile(r"\brename\b", re.IGNORECASE),
)

_SON_FILE_PATTERNS = (
    re.compile(r"son\s+dosya(?:n[ıi]n|nin)?", re.IGNORECASE),
    re.compile(r"son\s+olusturdugun\s+dosya(?:n[ıi]n|nin)?", re.IGNORECASE),
    re.compile(r"son\s+oluşturduğun\s+dosya(?:n[ıi]n|nin)?", re.IGNORECASE),
)

_OPEN_FILE_PATTERNS = (
    re.compile(r"(?:ac|aç|open)", re.IGNORECASE),
)

_NEW_TASK_SIGNALS = (
    re.compile(r"\b(kur|install|yukle|yükle)\b", re.IGNORECASE),
    re.compile(r"klas(?:ö|o)r(?:u|ü)?\s+(?:olustur|oluştur|yarat)", re.IGNORECASE),
    re.compile(r"masa[uü]st", re.IGNORECASE),
    re.compile(r"\b(analiz|optimize|github)\b", re.IGNORECASE),
)

_CONTINUATION_SIGNALS = (
    re.compile(r"^şimdi\s+a[çc]", re.IGNORECASE),
    re.compile(r"^simdi\s+a[çc]", re.IGNORECASE),
    re.compile(r"^onu\s+a[çc]", re.IGNORECASE),
    re.compile(r"^bunu\s+a[çc]", re.IGNORECASE),
    re.compile(r"^tekrar\s+a[çc]", re.IGNORECASE),
)


@dataclass
class ResolutionResult:
    resolved_references: dict[str, str] = field(default_factory=dict)
    intent: Any | None = None
    follow_up_intents: list[Any] = field(default_factory=list)
    ambiguous: bool = False
    clarification: str = ""
    is_new_task: bool = True
    is_continuation: bool = False


def _local_intent(name: str, arguments: dict, summary: str) -> Any:
    from hermes.agent.local_intent import LocalIntent, LocalToolRequest

    return LocalIntent(LocalToolRequest(name=name, arguments=arguments), summary=summary)


def _normalize_existing_path(path: str) -> Path | None:
    raw = (path or "").strip()
    if not raw:
        return None
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = Path.home() / "Desktop" / target
    try:
        target = target.resolve()
    except OSError:
        return None
    return target if target.exists() else None


def _build_open_path_result(path: str, *, kind: str = "file") -> ResolutionResult:
    resolved = _normalize_existing_path(path)
    if resolved is None:
        label = "Klasor" if kind == "folder" else "Dosya"
        return ResolutionResult(
            ambiguous=True,
            clarification=(
                f"{label} bulunamadi: {path}. Hangi {'klasoru' if kind == 'folder' else 'dosyayi'} acmami istiyorsun?"
            ),
            is_new_task=False,
        )
    resolved_str = str(resolved)
    summary = (
        f"Klasor acilacak: {resolved_str}"
        if kind == "folder"
        else f"Dosya acilacak: {resolved_str}"
    )
    refs = (
        {"target_folder": resolved_str}
        if kind == "folder"
        else {"target_file": resolved_str}
    )
    return ResolutionResult(
        intent=_local_intent("open_path", {"path": resolved_str}, summary),
        resolved_references=refs,
        is_new_task=False,
    )


class ReferenceResolver:
    """Deterministic reference resolution from conversational context."""

    def resolve(self, message: str, ctx: ConversationalContext) -> ResolutionResult:
        text = (message or "").strip()
        if not text:
            return ResolutionResult(is_new_task=True)

        from hermes.agent.application_catalog import has_explicit_filename, is_web_or_app_open_message

        if is_web_or_app_open_message(text) and not has_explicit_filename(text):
            return ResolutionResult(is_new_task=True)

        lower = text.casefold()
        from hermes.mission.selection import should_route_to_mission

        from hermes.context.parent_directory import is_parent_directory_open_message
        from hermes.context.file_intent import is_file_read_message

        if is_file_read_message(text):
            read_intent = self._resolve_read_file_content(text, lower, ctx)
            if read_intent.intent or read_intent.ambiguous:
                return read_intent

        if should_route_to_mission(text) and not is_parent_directory_open_message(text):
            return ResolutionResult(is_new_task=True)

        result = ResolutionResult(
            is_new_task=self._is_new_task(text, lower),
            is_continuation=self._is_continuation(text, lower),
        )

        if result.is_continuation and not result.is_new_task:
            continuation = self._resolve_continuation(text, lower, ctx)
            if continuation.intent or continuation.ambiguous:
                return continuation

        content_intent = self._resolve_content_modification(text, lower, ctx)
        if content_intent.intent or content_intent.ambiguous:
            return content_intent

        rename_intent = self._resolve_rename_file(text, lower, ctx)
        if rename_intent.intent or rename_intent.ambiguous:
            return rename_intent

        parent_dir_intent = self._resolve_open_parent_directory(text, lower, ctx)
        if parent_dir_intent.intent or parent_dir_intent.ambiguous:
            return parent_dir_intent

        open_intent = self._resolve_open_folder(text, lower, ctx)
        if open_intent.intent or open_intent.ambiguous:
            return open_intent

        open_intent = self._resolve_open_file(text, lower, ctx)
        if open_intent.intent or open_intent.ambiguous:
            return open_intent

        read_intent = self._resolve_read_file_content(text, lower, ctx)
        if read_intent.intent or read_intent.ambiguous:
            return read_intent

        list_intent = self._resolve_list_folder(text, lower, ctx)
        if list_intent.intent or list_intent.ambiguous:
            return list_intent

        folder_file_intent = self._resolve_folder_file_create(text, lower, ctx)
        if folder_file_intent.intent or folder_file_intent.ambiguous:
            return folder_file_intent

        same_folder_intent = self._resolve_same_folder_create(text, lower, ctx)
        if same_folder_intent.intent or same_folder_intent.ambiguous:
            return same_folder_intent

        create_intent = self._resolve_create_folder(text, lower)
        if create_intent.intent or create_intent.ambiguous:
            return create_intent

        refs = self._resolve_passive_references(text, lower, ctx)
        if refs.ambiguous:
            return refs
        if refs.resolved_references:
            result.resolved_references.update(refs.resolved_references)
            result.is_new_task = result.is_new_task and not bool(refs.resolved_references)
        return result

    def _is_new_task(self, text: str, lower: str) -> bool:
        if any(pattern.search(text) for pattern in _NEW_TASK_SIGNALS):
            if not any(pattern.search(text) for pattern in _FILE_REF_PATTERNS):
                if not any(pattern.search(text) for pattern in _FOLDER_REF_PATTERNS):
                    if not any(pattern.search(text) for pattern in _CONTENT_MODIFY_PATTERNS):
                        return True
        return False

    def _is_continuation(self, text: str, lower: str) -> bool:
        return any(pattern.search(text) for pattern in _CONTINUATION_SIGNALS)

    def _resolve_continuation(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        from hermes.agent.application_catalog import is_web_or_app_open_message

        if is_web_or_app_open_message(text):
            return ResolutionResult(is_new_task=True, is_continuation=False)

        if re.search(r"\b(a[çc]|open|baslat|başlat)\b", lower):
            if re.search(r"\b(onu|bunu|şunu|sunu|tekrar)\b", lower) or re.search(
                r"\bdosya(?:y[ıi])?\b", lower
            ):
                from hermes.context.agent_context import resolve_deictic_file

                resolved = resolve_deictic_file(ctx, text, intent="open_file")
                if resolved:
                    built = _build_open_path_result(resolved.path)
                    built.is_new_task = False
                    built.is_continuation = True
                    if built.intent or built.ambiguous:
                        return built
            if ctx.last_application:
                return ResolutionResult(
                    intent=_local_intent(
                        "open_app",
                        {"app": ctx.last_application},
                        f"{ctx.last_application} acilacak.",
                    ),
                    resolved_references={"target_application": ctx.last_application},
                    is_new_task=False,
                    is_continuation=True,
                )
            return ResolutionResult(
                ambiguous=True,
                clarification="Hangi uygulamayi acmami istiyorsun?",
                is_new_task=False,
                is_continuation=True,
            )
        return ResolutionResult(is_new_task=False, is_continuation=True)

    def _resolve_content_modification(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        from hermes.context.agent_context import extract_icine_write_content, resolve_context_file

        icine_content = extract_icine_write_content(text)
        from hermes.context.folder_reference import is_file_create_message

        if icine_content and is_file_create_message(text):
            return ResolutionResult()
        if icine_content and not re.search(r"\b(olustur|oluştur|yarat|create)\b", lower):
            resolved = resolve_context_file(text, ctx, prefer_modified=True, allow_stem=True)
            if resolved:
                existing = _normalize_existing_path(resolved.path)
                if existing:
                    return ResolutionResult(
                        intent=_local_intent(
                            "write_file",
                            {
                                "path": str(existing),
                                "content": icine_content,
                                "unique_if_exists": False,
                            },
                            f"Dosya guncellenecek: {Path(existing).name}",
                        ),
                        resolved_references={"target_file": str(existing)},
                        is_new_task=False,
                    )

        is_content_mod = any(pattern.search(text) for pattern in _CONTENT_MODIFY_PATTERNS) or (
            re.search(r"\b(degistir|değiştir)\b", lower) and re.search(r"dosya", lower)
        )
        if not is_content_mod:
            if not re.search(r"\b(içine|icerige|icerik)\b", lower) or re.search(
                r"\b(olustur|oluştur|yarat|create)\b", lower
            ):
                return ResolutionResult()

        if re.search(r"\b(olustur|oluştur|yarat|create)\b", lower) and re.search(
            r"\b(?:txt|dosya)\b", lower
        ):
            return ResolutionResult()

        target = self._pick_target_file(
            text, lower, ctx, prefer_modified=True, content_modify=True
        )
        if target.ambiguous:
            return target
        file_path = target.resolved_references.get("target_file")
        if not file_path:
            if any(pattern.search(text) for pattern in _CONTENT_MODIFY_PATTERNS):
                return ResolutionResult(
                    ambiguous=True,
                    clarification="Hangi dosyanin icerigini degistirmemi istiyorsun?",
                    is_new_task=False,
                )
            return ResolutionResult()

        existing = _normalize_existing_path(file_path)
        if existing is None:
            return ResolutionResult(
                ambiguous=True,
                clarification=(
                    f"Dosya bulunamadi: {file_path}. Hangi dosyanin icerigini degistirmemi istiyorsun?"
                ),
                is_new_task=False,
            )
        file_path = str(existing)

        content = extract_content_modification(text)
        if content is None:
            if re.search(r"\bdeğiştir\b|\bdegistir\b", lower) and not re.search(
                r"\b(yaz|yap)\b", lower
            ):
                return ResolutionResult(
                    ambiguous=True,
                    clarification=(
                        f"{Path(file_path).name} dosyasinin yeni icerigi ne olmali?"
                    ),
                    resolved_references={"target_file": file_path},
                    is_new_task=False,
                )
            return ResolutionResult()

        return ResolutionResult(
            intent=_local_intent(
                "write_file",
                {"path": file_path, "content": content, "unique_if_exists": False},
                f"Dosya guncellenecek: {file_path}",
            ),
            resolved_references={"target_file": file_path},
            is_new_task=False,
        )

    def _resolve_rename_file(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        from hermes.mission.compound_goal import is_compound_chained_file_mission

        if is_compound_chained_file_mission(text):
            return ResolutionResult()

        if not any(pattern.search(text) for pattern in _RENAME_PATTERNS):
            return ResolutionResult()
        if re.search(r"\b(olustur|oluştur|yaz|create)\b", lower) and not re.search(
            r"\badini\b|\badını\b", lower
        ):
            return ResolutionResult()

        new_name = _extract_rename_new_name(text, lower)
        if not new_name:
            return ResolutionResult(
                ambiguous=True,
                clarification="Dosyanin yeni adi ne olsun?",
                is_new_task=False,
            )

        source = self._pick_rename_source_file(text, lower, ctx, new_name=new_name)
        if source.ambiguous:
            return source
        file_path = source.resolved_references.get("target_file")
        if not file_path:
            return ResolutionResult(
                ambiguous=True,
                clarification="Hangi dosyanin adini degistirmemi istiyorsun?",
                is_new_task=False,
            )

        existing = _normalize_existing_path(file_path)
        if existing is None:
            return ResolutionResult(
                ambiguous=True,
                clarification=f"Dosya bulunamadi: {file_path}. Hangi dosyayi kastediyorsun?",
                is_new_task=False,
            )
        file_path = str(existing)
        clean_name = Path(new_name).name

        return ResolutionResult(
            intent=_local_intent(
                "rename_path",
                {"path": file_path, "new_name": clean_name},
                f"Dosya adi degistirilecek: {Path(file_path).name} -> {clean_name}",
            ),
            resolved_references={"target_file": file_path, "new_name": clean_name},
            is_new_task=False,
        )

    def _pick_rename_source_file(
        self,
        text: str,
        lower: str,
        ctx: ConversationalContext,
        *,
        new_name: str,
    ) -> ResolutionResult:
        explicit_name = _extract_explicit_rename_source_filename(text, new_name)
        if explicit_name:
            candidates: list[str] = []
            for base in (ctx.active_folder, ctx.last_created_folder):
                if base:
                    candidates.append(str(Path(base) / explicit_name))
            folder_name = extract_named_folder_name(text)
            if folder_name:
                candidates.append(str(Path.home() / "Desktop" / folder_name / explicit_name))
            for path in ctx.created_files + ctx.recent_files:
                if Path(path).name.casefold() == explicit_name.casefold():
                    candidates.append(path)
            for candidate in candidates:
                normalized = _normalize_existing_path(candidate)
                if normalized:
                    return ResolutionResult(
                        resolved_references={"target_file": str(normalized)}
                    )

        explicit = match_named_file(text, ctx.recent_files, allow_stem=False, ctx=ctx)
        if explicit.resolved_references.get("target_file"):
            path = explicit.resolved_references["target_file"]
            if Path(path).name.casefold() != Path(new_name).name.casefold():
                return explicit
        if explicit.ambiguous:
            return explicit

        if any(pattern.search(text) for pattern in _FILE_REF_PATTERNS) or any(
            pattern.search(text) for pattern in _SON_FILE_PATTERNS
        ):
            from hermes.context.agent_context import resolve_deictic_file

            resolved = resolve_deictic_file(ctx, text, intent="modify_file")
            if resolved:
                return ResolutionResult(resolved_references={"target_file": resolved.path})

        if ctx.active_file and _normalize_existing_path(ctx.active_file):
            return ResolutionResult(resolved_references={"target_file": ctx.active_file})
        from hermes.context.agent_context import resolve_deictic_file

        resolved = resolve_deictic_file(ctx, text, intent="modify_file")
        if resolved:
            return ResolutionResult(resolved_references={"target_file": resolved.path})

        return ResolutionResult()

    def _resolve_create_folder(self, text: str, lower: str) -> ResolutionResult:
        from hermes.context.folder_reference import (
            is_file_create_message,
            is_folder_create_message,
            is_folder_open_only_message,
            resolve_create_folder_path,
        )

        if is_file_create_message(text):
            return ResolutionResult()
        if is_folder_open_only_message(text):
            return ResolutionResult()
        if not is_folder_create_message(text):
            return ResolutionResult()

        folder_path = resolve_create_folder_path(text)
        if not folder_path:
            return ResolutionResult(
                ambiguous=True,
                clarification="Hangi isimle klasor olusturmami istiyorsun?",
                is_new_task=True,
            )
        return ResolutionResult(
            intent=_local_intent(
                "create_folder",
                {"path": folder_path},
                f"Klasor olusturulacak: {folder_path}",
            ),
            resolved_references={"target_folder": folder_path},
            is_new_task=True,
        )

    def _resolve_open_parent_directory(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        from hermes.context.parent_directory import (
            is_parent_directory_open_message,
            resolve_parent_directory_path,
        )

        if not is_parent_directory_open_message(text):
            return ResolutionResult()

        folder_path, error = resolve_parent_directory_path(text, ctx)
        if error:
            return ResolutionResult(
                ambiguous=True,
                clarification=error,
                is_new_task=False,
            )
        if not folder_path:
            return ResolutionResult()

        return _build_open_path_result(folder_path, kind="folder")

    def _resolve_open_folder(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        from hermes.context.folder_reference import is_folder_create_message
        from hermes.context.goal_resolution import parse_open_folder_goal

        if is_folder_create_message(text):
            return ResolutionResult()
        from hermes.context.parent_directory import is_parent_directory_open_message

        if is_parent_directory_open_message(text):
            return ResolutionResult()
        if not any(pattern.search(text) for pattern in _OPEN_FILE_PATTERNS):
            return ResolutionResult()
        if re.search(r"\b(olustur|oluştur|yaz|create)\b", lower) and not any(
            pattern.search(text) for pattern in _FOLDER_REF_PATTERNS
        ):
            return ResolutionResult()

        wants_folder = any(pattern.search(text) for pattern in _FOLDER_OPEN_PATTERNS) or re.search(
            r"klasor|klasör|folder|dizin", lower
        )
        if not wants_folder:
            return ResolutionResult()
        if re.search(r"dosya", lower) and not re.search(r"klasor|klasör|folder|dizin", lower):
            return ResolutionResult()

        from hermes.context.agent_context import resolve_deictic_folder, resolve_folder_by_priority

        if any(pattern.search(text) for pattern in _FOLDER_REF_PATTERNS):
            deictic = resolve_deictic_folder(ctx, text)
            if deictic:
                return _build_open_path_result(deictic, kind="folder")
            folder = resolve_folder_by_priority(ctx)
            if folder:
                return _build_open_path_result(folder, kind="folder")

        explicit = parse_open_folder_goal(text)
        if explicit is not None:
            return _build_open_path_result(str(explicit.path), kind="folder")

        from hermes.context.system_paths import resolve_known_folder

        known = resolve_known_folder(text)
        if known and known.exists():
            return _build_open_path_result(str(known), kind="folder")

        folder = resolve_folder_by_priority(ctx)
        if folder:
            return _build_open_path_result(folder, kind="folder")

        if re.search(r"klasor|klasör|folder|dizin", lower):
            return ResolutionResult(
                ambiguous=True,
                clarification="Hangi klasoru acmami istiyorsun?",
                is_new_task=False,
            )
        return ResolutionResult()

    def _resolve_open_file(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        from hermes.agent.application_catalog import has_explicit_filename, is_web_or_app_open_message

        if is_web_or_app_open_message(text) and not has_explicit_filename(text):
            return ResolutionResult()

        if not any(pattern.search(text) for pattern in _OPEN_FILE_PATTERNS):
            return ResolutionResult()
        if re.search(r"\b(olustur|oluştur|yaz|create)\b", lower):
            return ResolutionResult()

        from hermes.context.folder_reference import extract_explicit_filename

        file_name = extract_explicit_filename(text)
        if file_name:
            matches: list[str] = []
            seen: set[str] = set()

            def _add_match(path: str | None) -> None:
                if not path:
                    return
                key = str(Path(path).resolve())
                if key not in seen:
                    seen.add(key)
                    matches.append(key)

            for path in list(ctx.recent_files or []):
                if Path(str(path)).name.casefold() == file_name.casefold():
                    _add_match(_normalize_existing_path(str(path)))
            if ctx.active_file and Path(ctx.active_file).name.casefold() == file_name.casefold():
                _add_match(_normalize_existing_path(ctx.active_file))
            if ctx.active_folder:
                candidate = Path(ctx.active_folder) / file_name
                _add_match(_normalize_existing_path(str(candidate)))
            desktop_candidate = Path.home() / "Desktop" / file_name
            _add_match(_normalize_existing_path(str(desktop_candidate)))
            if len(matches) > 1:
                from hermes.context.file_decision import build_open_results_from_decision, decide_file_candidates

                decision = decide_file_candidates(
                    matches,
                    ctx,
                    text=text,
                    filename=file_name,
                    action="open",
                )
                primary, follow_ups = build_open_results_from_decision(
                    decision,
                    filename=file_name,
                    build_open_path_result=_build_open_path_result,
                )
                if primary is not None:
                    primary.follow_up_intents = follow_ups
                    return primary
            if len(matches) == 1:
                return _build_open_path_result(matches[0])

        if re.search(r"^tekrar\s+a[çc]", text.strip(), re.IGNORECASE) or re.search(
            r"\b(onu|bunu|şunu|sunu|tekrar)\b", lower
        ):
            from hermes.context.agent_context import resolve_deictic_file

            resolved = resolve_deictic_file(ctx, text, intent="open_file")
            if resolved:
                built = _build_open_path_result(resolved.path)
                if built.intent or built.ambiguous:
                    built.is_new_task = False
                    return built

        named = match_named_file(text, ctx.recent_files, ctx=ctx)
        if named.ambiguous:
            return named
        if named.resolved_references.get("target_file"):
            return _build_open_path_result(named.resolved_references["target_file"])

        if any(pattern.search(text) for pattern in _FILE_REF_PATTERNS) or re.search(
            r"dosya", lower
        ):
            if re.search(r"\b(onu|bunu|şunu|sunu|tekrar)\b", lower):
                from hermes.context.agent_context import resolve_deictic_file

                resolved = resolve_deictic_file(ctx, text, intent="open_file")
                if resolved:
                    built = _build_open_path_result(resolved.path)
                    if built.intent or built.ambiguous:
                        built.is_new_task = False
                        return built
            target = self._pick_target_file(text, lower, ctx, prefer_modified=False)
            if target.ambiguous:
                return target
            path = target.resolved_references.get("target_file")
            if path:
                return _build_open_path_result(path)
        if re.search(r"dosya", lower):
            return ResolutionResult(
                ambiguous=True,
                clarification="Hangi dosyayi acmami istiyorsun?",
                is_new_task=False,
            )
        return ResolutionResult()

    def _resolve_read_file_content(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        from hermes.context.file_intent import is_file_read_message, resolve_read_file_path

        if not is_file_read_message(text):
            return ResolutionResult()

        file_path = resolve_read_file_path(text, ctx)
        if not file_path:
            if re.search(r"\.txt\b|dosya", lower, re.IGNORECASE):
                return ResolutionResult(
                    ambiguous=True,
                    clarification="Hangi dosyanin icerigini okumami istiyorsun?",
                    is_new_task=False,
                )
            return ResolutionResult()

        return ResolutionResult(
            intent=_local_intent(
                "read_file",
                {"path": file_path},
                f"Dosya okunacak: {Path(file_path).name}",
            ),
            resolved_references={"target_file": file_path},
            is_new_task=False,
        )

    def _resolve_list_folder(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        if not re.search(
            r"\b(listele|goster|göster|list|goreb|göreb|icerig|içeriğ)\b", lower
        ):
            return ResolutionResult()
        if not re.search(r"\bklas(?:o|ö)r|dosya(?:lar)?\b", lower):
            return ResolutionResult()

        from hermes.context.system_paths import resolve_known_folder

        known = resolve_known_folder(text)
        if known and known.exists():
            folder = str(known)
            return ResolutionResult(
                intent=_local_intent(
                    "list_directory",
                    {"path": folder},
                    f"Klasor listelenecek: {folder}",
                ),
                resolved_references={"target_folder": folder},
                is_new_task=False,
            )

        folder = ctx.focus_container() or ctx.active_folder or ctx.last_created_folder
        if folder and (
            any(pattern.search(text) for pattern in _FOLDER_REF_PATTERNS)
            or re.search(r"\bbu\s+klas", lower)
        ):
            return ResolutionResult(
                intent=_local_intent(
                    "list_directory",
                    {"path": folder},
                    f"Klasor listelenecek: {folder}",
                ),
                resolved_references={"target_folder": folder},
                is_new_task=False,
            )
        if re.search(r"\bbu\s+klas", lower) and folder:
            return ResolutionResult(
                intent=_local_intent(
                    "list_directory",
                    {"path": folder},
                    f"Klasor listelenecek: {folder}",
                ),
                resolved_references={"target_folder": folder},
                is_new_task=False,
            )
        return ResolutionResult(
            ambiguous=True,
            clarification="Hangi klasordeki dosyalari listelememi istiyorsun?",
            is_new_task=False,
        )

    def _resolve_folder_file_create(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        from hermes.context.file_intent import (
            FileIntentKind,
            is_file_read_message,
            resolve_file_create_target,
        )
        from hermes.mission.write_content import is_composite_file_mission

        if is_composite_file_mission(text):
            return ResolutionResult()
        if is_file_read_message(text):
            return ResolutionResult()
        if re.search(
            r"klasor(?:u|ü|unu|ünü)?\s+(?:olustur|oluştur|yarat|create)|"
            r"klasör(?:u|ü|unu|ünü)?\s+(?:olustur|oluştur|yarat|create)|"
            r"folder\s+(?:olustur|oluştur|yarat|create)",
            lower,
        ):
            return ResolutionResult()
        if not re.search(r"\b(olustur|oluştur|yaz|create)\b", lower):
            return ResolutionResult()
        if not re.search(r"\b(?:txt|dosya|\.txt)\b", lower) and not re.search(
            r"\.txt\b", text, re.IGNORECASE
        ):
            return ResolutionResult()

        target = resolve_file_create_target(
            text,
            active_folder=ctx.focus_container() or ctx.active_folder,
            last_created_folder=ctx.last_created_folder,
        )
        if target is None:
            if re.search(r"\b(word|docx|\.docx)\b", lower):
                return ResolutionResult()
            if extract_named_folder_name(text) or message_uses_contextual_folder(text):
                return ResolutionResult(
                    ambiguous=True,
                    clarification="Hangi klasore dosya olusturmami istiyorsun?",
                    is_new_task=False,
                )
            return ResolutionResult()

        if (
            target.kind == FileIntentKind.WRITE_FILE
            and target.content is None
            and re.search(r"\b(yaz|write)\b", lower)
        ):
            return ResolutionResult(
                ambiguous=True,
                clarification=f"{target.filename} dosyasina ne yazmami istiyorsun?",
                resolved_references={
                    "target_folder": target.folder_path or "",
                    "target_file": target.path,
                },
                is_new_task=False,
            )

        tool_name = target.kind.value
        args: dict[str, str] = {"path": target.path}
        if tool_name == "write_file":
            args["content"] = target.content or ""

        return ResolutionResult(
            intent=_local_intent(
                tool_name,
                args,
                f"Dosya olusturulacak: {target.path}",
            ),
            resolved_references={
                "target_folder": target.folder_path or "",
                "target_file": target.path,
            },
            is_new_task=False,
        )

    def _resolve_same_folder_create(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        from hermes.context.file_intent import is_file_read_message

        if is_file_read_message(text):
            return ResolutionResult()
        if not any(pattern.search(text) for pattern in _FOLDER_REF_PATTERNS):
            return ResolutionResult()
        if not re.search(r"\b(olustur|oluştur|yaz|create)\b", lower):
            return ResolutionResult()

        folder = ctx.focus_container() or ctx.active_folder or ctx.last_created_folder
        if not folder:
            return ResolutionResult(
                ambiguous=True,
                clarification="Hangi klasore dosya olusturmami istiyorsun?",
                is_new_task=False,
            )

        file_name = _extract_new_filename(text, lower)
        if not file_name:
            return ResolutionResult(
                ambiguous=True,
                clarification="Ayni klasore hangi dosyayi olusturmami istiyorsun?",
                resolved_references={"target_folder": folder},
                is_new_task=False,
            )

        file_path = str(Path(folder) / file_name)
        content = extract_content_modification(text) or extract_literal_write_content(text).literal_content
        if content is None and re.search(r"\b(yaz|write)\b", lower):
            return ResolutionResult(
                ambiguous=True,
                clarification=f"{file_name} dosyasina ne yazmami istiyorsun?",
                resolved_references={"target_folder": folder, "target_file": file_path},
                is_new_task=False,
            )
        args: dict[str, str] = {"path": file_path, "content": content or ""}

        return ResolutionResult(
            intent=_local_intent("write_file", args, f"Dosya olusturulacak: {file_path}"),
            resolved_references={"target_folder": folder, "target_file": file_path},
            is_new_task=False,
        )

    def _resolve_passive_references(
        self, text: str, lower: str, ctx: ConversationalContext
    ) -> ResolutionResult:
        refs: dict[str, str] = {}
        wants_file = any(pattern.search(text) for pattern in _FILE_REF_PATTERNS)
        wants_folder = any(pattern.search(text) for pattern in _FOLDER_REF_PATTERNS)

        if wants_file:
            picked = self._pick_target_file(text, lower, ctx, prefer_modified=False)
            if picked.ambiguous:
                return picked
            refs.update(picked.resolved_references)

        if wants_folder:
            folder = ctx.active_folder or ctx.last_created_folder
            if folder:
                refs["target_folder"] = folder
            elif wants_folder and not wants_file:
                return ResolutionResult(
                    ambiguous=True,
                    clarification="Hangi klasoru kastediyorsun?",
                    is_new_task=False,
                )

        return ResolutionResult(resolved_references=refs, is_new_task=not bool(refs))

    def _pick_target_file(
        self,
        text: str,
        lower: str,
        ctx: ConversationalContext,
        *,
        prefer_modified: bool,
        content_modify: bool = False,
    ) -> ResolutionResult:
        if not content_modify:
            named = match_named_file(text, ctx.recent_files, allow_stem=False)
            if named.resolved_references.get("target_file") or named.ambiguous:
                return named

        if content_modify or prefer_modified:
            from hermes.context.agent_context import resolve_deictic_file

            resolved = resolve_deictic_file(
                ctx, text, intent="modify_file" if prefer_modified else "open_file"
            )
            if resolved:
                return ResolutionResult(resolved_references={"target_file": resolved.path})

        if not content_modify:
            if prefer_modified and ctx.active_file:
                existing = _normalize_existing_path(ctx.active_file)
                if existing:
                    return ResolutionResult(resolved_references={"target_file": str(existing)})

            if not prefer_modified and re.search(
                r"son\s+olustur|son\s+oluştur|az\s+once(?:ki)?\s+olustur|az\s+önce(?:ki)?\s+olustur|son\s+dosya",
                lower,
            ):
                from hermes.context.agent_context import resolve_deictic_file

                resolved = resolve_deictic_file(ctx, text, intent="open_file")
                if resolved:
                    return ResolutionResult(resolved_references={"target_file": resolved.path})
                if ctx.last_created_file:
                    return ResolutionResult(
                        ambiguous=True,
                        clarification=(
                            f"Son olusturulan dosya artik mevcut degil: {ctx.last_created_file}. "
                            "Hangi dosyayi kastediyorsun?"
                        ),
                    )
                return ResolutionResult(
                    ambiguous=True,
                    clarification="Son olusturulan dosya bulunamadi. Hangi dosyayi kastediyorsun?",
                )

            stem_named = match_named_file(text, ctx.recent_files, allow_stem=True, ctx=ctx)
            if stem_named.resolved_references.get("target_file") or stem_named.ambiguous:
                return stem_named

            if prefer_modified and ctx.last_modified_file:
                if _normalize_existing_path(ctx.last_modified_file):
                    return ResolutionResult(
                        resolved_references={"target_file": ctx.last_modified_file}
                    )
            from hermes.context.agent_context import resolve_deictic_file

            resolved = resolve_deictic_file(
                ctx, text, intent="modify_file" if prefer_modified else "open_file"
            )
            if resolved:
                return ResolutionResult(resolved_references={"target_file": resolved.path})
            if len(ctx.recent_files) == 1:
                return ResolutionResult(resolved_references={"target_file": ctx.recent_files[0]})

        if any(pattern.search(text) for pattern in _FILE_REF_PATTERNS) or any(
            pattern.search(text) for pattern in _CONTENT_MODIFY_PATTERNS
        ):
            if len(ctx.recent_files) > 1:
                from hermes.context.file_decision import decide_file_candidates

                decision = decide_file_candidates(
                    list(ctx.recent_files),
                    ctx,
                    text=text,
                    action="modify" if prefer_modified else "open",
                )
                if decision.chosen_paths:
                    return ResolutionResult(
                        resolved_references={"target_file": decision.chosen_paths[0]}
                    )
                if decision.clarification:
                    return ResolutionResult(
                        ambiguous=True,
                        clarification=decision.clarification,
                        resolved_references={"candidates": "|".join(decision.candidates)},
                        is_new_task=False,
                    )
            return ResolutionResult(
                ambiguous=True,
                clarification="Hangi dosyayi kastediyorsun?",
            )
        return ResolutionResult()


def match_named_file(
    text: str,
    recent_files: list[str],
    *,
    allow_stem: bool = True,
    ctx: ConversationalContext | None = None,
) -> ResolutionResult:
    lower = text.casefold()
    matches: list[str] = []
    for path in recent_files:
        name = Path(path).name.casefold()
        stem = Path(path).stem.casefold()
        if name and re.search(rf"\b{re.escape(name)}\b", lower):
            matches.append(path)
        elif allow_stem and stem and len(stem) >= 2 and re.search(
            rf"\b{re.escape(stem)}\b", lower
        ):
            matches.append(path)
    if len(matches) == 1:
        return ResolutionResult(resolved_references={"target_file": matches[0]})
    if len(matches) > 1 and ctx is not None:
        from hermes.context.file_decision import build_open_results_from_decision, decide_file_candidates
        from hermes.context.folder_reference import extract_explicit_filename

        filename = extract_explicit_filename(text) or Path(matches[0]).name
        decision = decide_file_candidates(
            matches,
            ctx,
            text=text,
            filename=filename,
            action="open",
        )
        primary, follow_ups = build_open_results_from_decision(
            decision,
            filename=filename,
            build_open_path_result=_build_open_path_result,
        )
        if primary is not None:
            primary.follow_up_intents = follow_ups
            return primary
    if len(matches) > 1:
        from hermes.context.file_decision import human_path_label

        preview = ", ".join(human_path_label(item) for item in matches[:3])
        return ResolutionResult(
            ambiguous=True,
            clarification=(
                f"Ayni isimli birkac dosya var ({preview}). Hangisini kastediyorsun?"
            ),
            resolved_references={"candidates": "|".join(matches[:6])},
        )
    return ResolutionResult()


def _extract_explicit_rename_source_filename(text: str, new_name: str) -> str | None:
    patterns = (
        r"([\w\d_.\-şğüöçıİŞĞÜÖÇ]+\.(?:txt|md|docx))\s+dosyas(?:ının|inin|inin|yı|yi|yi)\s+ad",
        r"([\w\d_.\-şğüöçıİŞĞÜÖÇ]+\.(?:txt|md|docx))\s+dosya(?:sının|sinin|sinin|sini|sini)\s+ad",
    )
    new_stem = Path(new_name).name.casefold()
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = Path(match.group(1)).name
            if candidate.casefold() != new_stem:
                return candidate
    return None


def _extract_rename_new_name(text: str, lower: str) -> str | None:
    patterns = (
        r"(?:adini|adını)\s+([\w\d_.\-şğüöçıİŞĞÜÖÇ]+?\.(?:txt|md|docx))\s+(?:yap|koy|ver|olsun)",
        r"(?:adini|adını)\s+([\w\d_.\-şğüöçıİŞĞÜÖÇ]+?)\s+(?:yap|koy|ver|olsun)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" .'\"")
            if candidate:
                if "." not in candidate:
                    candidate = f"{candidate}.txt"
                return candidate
    match = re.search(r"\b([\w\d_.\-şğüöçıİŞĞÜÖÇ]+\.(?:txt|md|docx))\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _extract_new_filename(text: str, lower: str) -> str | None:
    match = re.search(r"\b([\w\d_.-]+\.(?:txt|md|docx))\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(
        r"(?:olustur|oluştur|yarat|create)\s+(?:ve\s+)?(?:içine\s+)?([\w\d_.-]+)\s+(?:dosya|txt)",
        text,
        re.IGNORECASE,
    )
    if match:
        name = match.group(1)
        if not name.lower().endswith(".txt"):
            name = f"{name}.txt"
        return name
    match = re.search(r"\b([\w\d_.-]+)\.txt\b", text, re.IGNORECASE)
    if match:
        return match.group(0)
    return None


def _looks_like_path(text: str) -> bool:
    return bool(re.search(r"[\\/]|\.txt\b|\.docx\b", text, re.IGNORECASE))
