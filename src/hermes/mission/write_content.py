from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# Natural-language meta instructions — never belong in file content.
_META_PHRASE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"başka\s+hiçbir\s+şey\s+yaz(?:ma)?", re.IGNORECASE),
    re.compile(r"baska\s+hicbir\s+sey\s+yaz(?:ma)?", re.IGNORECASE),
    re.compile(r"başka\s+bir\s+şey\s+yaz(?:ma)?", re.IGNORECASE),
    re.compile(r"baska\s+bir\s+sey\s+yaz(?:ma)?", re.IGNORECASE),
    re.compile(r"hiçbir\s+şey\s+yazma", re.IGNORECASE),
    re.compile(r"hicbir\s+sey\s+yazma", re.IGNORECASE),
    re.compile(r"nothing\s+else", re.IGNORECASE),
    re.compile(r"do\s+not\s+write\s+anything\s+else", re.IGNORECASE),
    re.compile(r"only\s+this\s+text", re.IGNORECASE),
)

_META_CONTENT_REJECT = (
    re.compile(r"^işlemleri\s+sırayla", re.IGNORECASE),
    re.compile(r"^islem(?:leri)?\s+sirayla", re.IGNORECASE),
    re.compile(r"^dosyay(?:ı|i)\s+oluştur", re.IGNORECASE),
    re.compile(r"^dosyay(?:ı|i)\s+olustur", re.IGNORECASE),
    re.compile(r"^oluşturduğunu\s+doğrula", re.IGNORECASE),
    re.compile(r"^olusturdugunu\s+dogrula", re.IGNORECASE),
)

_WRITE_INTENT_PATTERN = re.compile(
    r"\b(?:txt|\.txt|metin|text\s+file|dosya(?:y[ıi])?\s+(?:olustur|oluştur|yaz)|"
    r"içine\s+.+\s+yaz|icerik)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WriteContentHint:
    """Structured separation of literal file content vs meta instructions in user text."""

    literal_content: str | None = None
    literal_source: str | None = None
    meta_instructions: tuple[str, ...] = field(default_factory=tuple)

    def to_planning_dict(self) -> dict[str, object]:
        return {
            "literal_content": self.literal_content,
            "literal_source": self.literal_source,
            "meta_instructions": list(self.meta_instructions),
        }


def planner_debug_enabled() -> bool:
    value = (os.environ.get("HERMES_PLANNER_DEBUG") or "").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def detect_meta_instructions(text: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _META_PHRASE_PATTERNS:
        for match in pattern.finditer(text or ""):
            phrase = match.group(0).strip()
            key = phrase.casefold()
            if key not in seen:
                seen.add(key)
                found.append(phrase)
    return tuple(found)


def _extract_backtick_literal(text: str) -> str | None:
    match = re.search(r"`([^`]+)`", text or "")
    if match:
        return match.group(1)
    return None


def _extract_quoted_literal(text: str) -> str | None:
    patterns = (
        r"(?:içine|icerige|icerik|content)\s+[\"']([^\"']+)[\"']",
        r"(?:tam\s+olarak|exactly)\s+[\"']([^\"']+)[\"']",
        r"[\"']([^\"']+)[\"']\s+yaz",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_exact_marker_literal(text: str) -> str | None:
    match = re.search(
        r"(?:tam\s+olarak|exactly|sadece|yalnızca|yalnizca)\s+(\S+)",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    candidate = match.group(1).strip(" .;,\"'`")
    if not candidate or candidate.startswith("`"):
        return None
    return candidate


def _clean_extracted_write_content(raw: str, *, full_text: str) -> str | None:
    content = (raw or "").strip(" .")
    if not content:
        return None
    if re.search(
        r"\b(dosya(?:s[ıi])?|klasor|klasör|olustur|oluştur|create|txt)\b",
        content,
        re.IGNORECASE,
    ):
        return None
    for phrase in detect_meta_instructions(full_text):
        content = re.sub(re.escape(phrase), "", content, flags=re.IGNORECASE).strip(" .")
    for pattern in _META_CONTENT_REJECT:
        if pattern.search(content.strip()):
            return None
    if not content or content.casefold() in {"yaz", "yap", "olustur", "oluştur", "create"}:
        return None
    return content


def _extract_natural_inline_literal(text: str) -> str | None:
    starter_pattern = re.compile(
        r"(?:içine|icerige|icerik|dosyaya|dosyan(?:ın|in)\s+içine|dosyanin\s+icine)\s+",
        re.IGNORECASE,
    )
    content_pattern = re.compile(
        r"(?:(?:tam\s+olarak|sadece)\s+)?(.+?)\s+yaz",
        re.IGNORECASE,
    )
    candidates: list[str] = []
    for starter in starter_pattern.finditer(text or ""):
        match = content_pattern.match(text[starter.end() :])
        if match:
            candidates.append(match.group(1))
    for raw in reversed(candidates):
        cleaned = _clean_extracted_write_content(raw, full_text=text)
        if cleaned:
            return cleaned
    return None


def is_placeholder_write_content(content: str | None) -> bool:
    text = (content or "").strip().casefold()
    if not text:
        return True
    markers = (
        "hermes tarafindan olusturuldu",
        "hermes tarafından oluşturuldu",
    )
    return any(marker in text for marker in markers)


def extract_literal_write_content(text: str) -> WriteContentHint:
    """
    Parse user natural language for the literal bytes/text to write.

    Priority: backticks > explicit quotes > exact-marker token.
    Meta phrases are detected separately and must never become file content.
    """
    meta = detect_meta_instructions(text)
    for extractor, source in (
        (_extract_backtick_literal, "backticks"),
        (_extract_quoted_literal, "quotes"),
        (_extract_natural_inline_literal, "natural_inline"),
        (_extract_exact_marker_literal, "exact_marker"),
    ):
        literal = extractor(text)
        if literal is not None:
            cleaned = literal if source == "exact_marker" else _clean_extracted_write_content(literal, full_text=text) or literal
            if cleaned:
                return WriteContentHint(
                    literal_content=cleaned,
                    literal_source=source,
                    meta_instructions=meta,
                )
    return WriteContentHint(literal_content=None, literal_source=None, meta_instructions=meta)


def has_write_content_intent(text: str) -> bool:
    return bool(_WRITE_INTENT_PATTERN.search(text or ""))


def content_contains_meta(content: str, meta_instructions: tuple[str, ...]) -> bool:
    lowered = (content or "").casefold()
    for phrase in meta_instructions:
        if phrase.casefold() in lowered:
            return True
    return False


def resolve_write_file_content(user_goal: str, proposed: str | None = None) -> str | None:
    """Return resolved file content or None when user intent cannot be determined safely."""
    hint = extract_literal_write_content(user_goal)
    if hint.literal_content is not None:
        return hint.literal_content
    if proposed and proposed.strip():
        if hint.meta_instructions and content_contains_meta(proposed, hint.meta_instructions):
            return None
        if is_placeholder_write_content(proposed):
            return None
        return proposed.strip()
    return None


def normalize_write_file_arguments(
    user_goal: str,
    arguments: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """
    Align write_file.content with structured literal extraction from user_goal.

    Does not mutate security/policy — only normalizes planner/local intent arguments.
    """
    hint = extract_literal_write_content(user_goal)
    args = dict(arguments)
    original = str(args.get("content") or "")
    diagnostic: dict[str, object] = {
        "tool": "write_file",
        "path": args.get("path"),
        "original_content": original,
        "write_content_hint": hint.to_planning_dict(),
        "normalized": False,
    }

    if hint.literal_content is not None:
        if original != hint.literal_content:
            diagnostic["normalized"] = True
            diagnostic["reason"] = "literal_from_user_goal"
        args["content"] = hint.literal_content
    elif hint.meta_instructions and content_contains_meta(original, hint.meta_instructions):
        diagnostic["warning"] = "content_contains_meta_instructions"
        diagnostic["reason"] = "meta_phrase_in_content_without_literal_hint"

    diagnostic["final_content"] = args.get("content")
    return args, diagnostic


def build_write_content_planning_hints(user_goal: str) -> dict[str, object] | None:
    """Optional planner context payload for write_file argument generation."""
    if not has_write_content_intent(user_goal):
        return None
    hint = extract_literal_write_content(user_goal)
    payload: dict[str, object] = {
        "write_content_hint": hint.to_planning_dict(),
        "rules": [
            "write_file.content must contain ONLY the literal text the user wants inside the file",
            "Never put meta instructions (e.g. 'başka hiçbir şey yazma', 'tam olarak') into content",
            "When user gives backticks or quotes, copy the inner text exactly with no extra characters",
            "Meta instructions belong in step title/expected_result, not in tool_arguments.content",
        ],
    }
    if hint.literal_content is not None:
        payload["required_content"] = hint.literal_content
    return payload


def requires_tool_output_dependency(text: str) -> bool:
    """
    True when the user goal needs data from a prior tool (not literal file content).
    """
    lower = (text or "").casefold()
    wants_file_write = bool(
        re.search(r"dosya(?:y[ıi])?\s+(?:olustur|oluştur|yaz)|\.txt|\btxt\b|dosyaya\s+yaz", lower)
    )
    if not wants_file_write:
        return False
    if extract_literal_write_content(text).literal_content is not None:
        return False

    dynamic_signals = (
        "öğren",
        "ogren",
        "topla",
        "al ve yaz",
        "sistem bilgi",
        "windows sürüm",
        "windows surum",
        "windows version",
        "bilgisayar ad",
        "işlemci",
        "islemci",
        "ram",
        "cpu",
        "dinamik",
        "gerçek",
        "gercek",
        "mevcut bilgi",
        "sistem.txt",
        "ozet",
        "özet",
        "ozetle",
        "özetle",
        "summarize",
        "summary",
        "pdf",
        "oku",
        "read",
        "extract",
        "rapor ozet",
        "rapor özet",
    )
    return any(signal in lower for signal in dynamic_signals)


def is_literal_composite_file_mission(text: str) -> bool:
    """Fast-path composite file mission with known literal content only."""
    from hermes.agent.implicit_file_content import requires_implicit_content_generation
    from hermes.mission.compound_goal import is_compound_chained_file_mission

    if requires_implicit_content_generation(text):
        return False
    if is_compound_chained_file_mission(text):
        return False
    if not is_composite_file_mission(text):
        return False
    return not requires_tool_output_dependency(text)


def is_composite_file_mission(text: str) -> bool:
    """True when user asks to create a folder and write a file in one goal."""
    lower = (text or "").casefold()
    wants_folder_create = bool(
        re.search(r"klasor|klasör|folder", lower)
        and re.search(
            r"klasor(?:u|ü|unu|ünü)?\s+(?:olustur|oluştur|yarat|create)|"
            r"klasör(?:u|ü|unu|ünü)?\s+(?:olustur|oluştur|yarat|create)|"
            r"folder\s+(?:olustur|oluştur|yarat|create)",
            lower,
        )
    )
    wants_write = bool(
        re.search(r"\.txt|\btxt\b|metin\s+dosya|dosya(?:y[ıi])?\s+(?:olustur|oluştur)", lower)
        or extract_literal_write_content(text).literal_content
    )
    return wants_folder_create and wants_write


def plan_composite_file_sequence(message: str) -> list:
    """Build create_folder + write_file steps for literal composite desktop file missions."""
    from pathlib import Path

    from hermes.agent.local_intent import LocalIntent, LocalToolRequest, _extract_desktop_path
    from hermes.context.folder_reference import extract_named_folder_name, extract_new_filename

    if not is_literal_composite_file_mission(message):
        return []

    text = (message or "").strip()
    lower = text.casefold()
    desktop = Path.home() / "Desktop"

    folder_name = extract_named_folder_name(text)
    if not folder_name:
        folder_match = re.search(
            r"(?:masa[uü]st(?:u|ü)(?:nde|de)?\s+)?([\w\d_-]+)\s+klas[öo]r",
            text,
            re.IGNORECASE,
        )
        folder_name = folder_match.group(1) if folder_match else "Hermes"
    folder_path = str(desktop / folder_name)

    rel_path = _extract_desktop_path(text, lower)
    if rel_path:
        rel = Path(rel_path.replace("\\", "/"))
        if rel.is_absolute():
            file_path = str(rel)
        elif len(rel.parts) >= 2:
            file_path = str(desktop / rel)
        else:
            file_path = str(desktop / folder_name / rel.name)
    else:
        file_name = extract_new_filename(text) or "test.txt"
        file_path = str(desktop / folder_name / file_name)

    content = resolve_write_file_content(text)
    if content is None or is_placeholder_write_content(content):
        return []

    steps: list = []
    if not Path(folder_path).exists():
        steps.append(
            LocalIntent(
                LocalToolRequest("create_folder", {"path": folder_path}),
                summary=f"Klasor olusturulacak: {folder_path}",
            )
        )
    steps.append(
        LocalIntent(
            LocalToolRequest("write_file", {"path": file_path, "content": content}),
            summary=f"Metin dosyasi yazilacak: {file_path}",
        )
    )
    return steps


def normalize_plan_steps_write_content(
    user_goal: str,
    steps: list,
) -> tuple[list, list[dict[str, object]]]:
    """Normalize write_file steps after planner validation. Returns steps + diagnostics."""
    from dataclasses import replace

    if requires_tool_output_dependency(user_goal) and extract_literal_write_content(user_goal).literal_content is None:
        return steps, []

    diagnostics: list[dict[str, object]] = []
    normalized = []
    for step in steps:
        if getattr(step, "tool_name", None) == "write_file":
            new_args, diag = normalize_write_file_arguments(user_goal, step.tool_arguments)
            diagnostics.append({**diag, "step_id": step.step_id})
            step = replace(step, tool_arguments=new_args)
        normalized.append(step)
    return normalized, diagnostics
