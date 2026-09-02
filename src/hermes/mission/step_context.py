from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hermes.mission.models import Mission, MissionStep, MissionStepStatus
from hermes.utils.logging import get_logger

logger = get_logger(__name__)

PLACEHOLDER_CONTENT_MARKERS = (
    "hermes tarafindan olusturuldu",
    "hermes tarafından oluşturuldu",
)

LOGICAL_KIND_FORMAT_SYSTEM_INFO = "format_system_info_for_file"

SOURCE_FIELD_PREPARED_CONTENT = "prepared_content"
SOURCE_FIELD_TOOL_OUTPUT_FORMATTED = "tool_output_formatted"
SOURCE_FIELD_TOOL_DESTINATION = "destination"


def is_placeholder_content(content: str) -> bool:
    text = (content or "").strip().casefold()
    if not text:
        return True
    return any(marker in text for marker in PLACEHOLDER_CONTENT_MARKERS)


def extract_folder_and_file_paths(user_goal: str) -> tuple[str, str]:
    from hermes.agent.local_intent import _extract_desktop_path
    from hermes.context.folder_reference import extract_named_folder_name, extract_new_filename

    text = (user_goal or "").strip()
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
        rel = Path(str(rel_path).replace("\\", "/"))
        if rel.is_absolute():
            file_path = str(rel)
        elif len(rel.parts) >= 2:
            file_path = str(desktop / rel)
        else:
            file_path = str(desktop / folder_name / rel.name)
    else:
        file_name = extract_new_filename(text) or "sistem.txt"
        file_path = str(desktop / folder_name / file_name)
    return folder_path, file_path


def format_system_info_file_content(info: dict[str, Any]) -> str:
    windows = info.get("windows") if isinstance(info.get("windows"), dict) else {}
    computer = info.get("computer") if isinstance(info.get("computer"), dict) else {}

    platform = str(info.get("platform") or windows.get("Caption") or "").strip()
    version = str(windows.get("Version") or "").strip()
    build = str(windows.get("BuildNumber") or "").strip()
    hostname = str(info.get("hostname") or computer.get("Name") or computer.get("CSName") or "").strip()
    processor = str(info.get("processor") or "").strip()
    cpu_count = info.get("cpu_count")
    ram_gb = info.get("ram_total_gb")

    lines = ["Sistem Bilgileri", "=" * 40]
    if platform or version or build:
        windows_line = "Windows: " + " ".join(
            part for part in (platform, version, f"(Build {build})" if build else "") if part
        )
        lines.append(windows_line.strip())
    if hostname:
        lines.append(f"Bilgisayar Adi: {hostname}")
    if processor:
        lines.append(f"Islemci: {processor}")
    if cpu_count:
        lines.append(f"CPU Cekirdek: {cpu_count}")
    if ram_gb is not None:
        lines.append(f"RAM: {ram_gb} GB")
    return "\n".join(lines)


def missing_system_info_fields(info: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    windows = info.get("windows") if isinstance(info.get("windows"), dict) else {}
    computer = info.get("computer") if isinstance(info.get("computer"), dict) else {}
    has_windows = bool(info.get("platform") or windows.get("Caption") or windows.get("Version"))
    has_hostname = bool(info.get("hostname") or computer.get("Name") or computer.get("CSName"))
    has_cpu = bool(info.get("processor") or info.get("cpu_count"))
    has_ram = info.get("ram_total_gb") is not None
    if not has_windows:
        missing.append("windows_version")
    if not has_hostname:
        missing.append("hostname")
    if not has_cpu:
        missing.append("cpu")
    if not has_ram:
        missing.append("ram")
    return missing


def system_info_output_valid(info: dict[str, Any]) -> bool:
    return not missing_system_info_fields(info)


def prepared_system_info_content_valid(content: str, info: dict[str, Any]) -> bool:
    text = (content or "").casefold()
    if not text.strip():
        return False
    windows = info.get("windows") if isinstance(info.get("windows"), dict) else {}
    computer = info.get("computer") if isinstance(info.get("computer"), dict) else {}
    hostname = str(info.get("hostname") or computer.get("Name") or computer.get("CSName") or "")
    processor = str(info.get("processor") or "")
    platform = str(info.get("platform") or windows.get("Caption") or "")
    ram = info.get("ram_total_gb")

    if hostname and hostname.casefold() not in text:
        return False
    if processor and processor.casefold() not in text and str(info.get("cpu_count") or "") not in text:
        return False
    if platform and platform.casefold() not in text and str(windows.get("Version") or "") not in text:
        return False
    if ram is not None and str(ram) not in text:
        return False
    return True


def dependency_closure(step: MissionStep, mission: Mission) -> list[str]:
    """Return step_ids in dependency order ending with the given step."""
    steps_by_id = {item.step_id: item for item in mission.steps}
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in seen:
            return
        dep_step = steps_by_id.get(step_id)
        if dep_step is None:
            return
        for dep_id in dep_step.depends_on:
            visit(dep_id)
        if step_id not in seen:
            ordered.append(step_id)
            seen.add(step_id)

    visit(step.step_id)
    return ordered


def record_step_tool_output(mission: Mission, step: MissionStep, output: Any) -> None:
    bucket = mission.working_context.setdefault("step_outputs", {})
    bucket[step.step_id] = {
        "tool_name": step.tool_name,
        "output": output,
    }
    if isinstance(output, dict):
        step.metadata["tool_output"] = output


def scan_files_on_filesystem(
    path: str,
    pattern: str = "*",
    *,
    modified_within_hours: int | None = None,
) -> dict[str, Any]:
    """Filesystem-backed search used as verification source of truth."""
    from datetime import datetime, timedelta, timezone

    from hermes.tools.windows.file_tools import resolve_user_path

    raw = (path or "").strip()
    glob_pat = (pattern or "*").strip()
    folder = resolve_user_path(raw) if raw else Path.home() / "Downloads"
    if not folder.is_dir():
        return {
            "folder": str(folder),
            "pattern": glob_pat,
            "count": 0,
            "matches": [],
            "error": f"Klasor yok: {folder}",
        }

    cutoff = None
    if modified_within_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=int(modified_within_hours))

    matches: list[dict[str, Any]] = []
    for item in folder.rglob(glob_pat.lstrip("/")):
        if not item.is_file():
            continue
        if cutoff is not None:
            mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue
        matches.append(
            {
                "path": str(item),
                "name": item.name,
                "size": item.stat().st_size,
                "modified": item.stat().st_mtime,
            }
        )
    return {
        "folder": str(folder),
        "pattern": glob_pat,
        "count": len(matches),
        "matches": matches[:100],
    }


def file_has_extension(path_or_name: str, ext: str) -> bool:
    suffix = (ext if ext.startswith(".") else f".{ext}").casefold()
    return Path(path_or_name).suffix.casefold() == suffix


def is_pdf_path(path: str) -> bool:
    return Path(path).suffix.casefold() == ".pdf"


def is_pdf_match(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    path = str(item.get("path") or "").strip()
    name = str(item.get("name") or Path(path).name).strip()
    return is_pdf_path(name) or (path and is_pdf_path(path))


def pattern_requires_pdf(pattern: str) -> bool:
    return "pdf" in (pattern or "*").casefold()


def user_goal_preserves_folder_structure(user_goal: str) -> bool:
    goal = (user_goal or "").casefold()
    markers = (
        "klasor yapisini koru",
        "klasör yapısını koru",
        "folder structure",
        "klasor yapisini koruyarak",
        "klasör yapısını koruyarak",
        "alt klasor yapis",
        "alt klasör yapı",
    )
    return any(marker in goal for marker in markers)


def filter_search_matches_for_pattern(
    matches: list[dict[str, Any]],
    pattern: str,
) -> list[dict[str, Any]]:
    """Keep only files that satisfy the declared glob (e.g. *.pdf)."""
    if not pattern_requires_pdf(pattern):
        return [item for item in matches if isinstance(item, dict)]
    filtered: list[dict[str, Any]] = []
    for item in matches:
        if is_pdf_match(item):
            filtered.append(item)
    return filtered


def exclude_paths_under_folder(
    matches: list[dict[str, Any]],
    folder: Path | str,
) -> list[dict[str, Any]]:
    """Drop search matches already inside a destination subfolder (idempotent PDF inspect)."""
    try:
        root = Path(folder).resolve()
    except OSError:
        return matches
    if not root.is_dir() and not str(folder).strip():
        return matches
    filtered: list[dict[str, Any]] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        try:
            if Path(raw).resolve().is_relative_to(root):
                continue
        except (ValueError, OSError):
            pass
        filtered.append(item)
    return filtered


def select_copy_matches(
    matches: list[dict[str, Any]],
    pattern: str,
    *,
    mission_id: str | None = None,
    step_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Second safety filter at copy time — never pass non-PDF when pattern is PDF."""
    pdf_only = pattern_requires_pdf(pattern)
    accepted: list[dict[str, Any]] = []
    skipped: list[str] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        if pdf_only and not is_pdf_match(item):
            skipped.append(path)
            logger.info(
                "NON_PDF_MATCH_SKIPPED",
                path=path,
                pattern=pattern,
                mission_id=mission_id,
                step_id=step_id,
            )
            continue
        accepted.append(item)
    return accepted, skipped


def _destination_is_folder(raw: str, resolved: Path) -> bool:
    text = (raw or "").strip()
    if text.endswith(("/", "\\")):
        return True
    if resolved.is_dir():
        return True
    if not resolved.exists() and not resolved.suffix:
        return True
    return False


def resolve_destination_folder(destination: str) -> Path:
    """Resolve the target folder for copy_search_matches (always a directory)."""
    from hermes.tools.windows.file_tools import resolve_user_path

    raw = (destination or "").strip()
    resolved = resolve_user_path(raw)
    if _destination_is_folder(raw, resolved):
        return resolved
    return resolved.parent


def expected_copy_destination(
    source: str,
    destination: str,
    *,
    preserve_structure: bool = False,
    source_root: str | None = None,
) -> Path:
    """Resolve flat destination file path unless preserve_structure is requested."""
    from hermes.tools.windows.file_tools import resolve_user_path

    src = resolve_user_path(source)
    dest_folder = resolve_destination_folder(destination)

    if preserve_structure and source_root:
        root = resolve_user_path(source_root)
        try:
            relative = src.relative_to(root)
            return dest_folder / relative
        except ValueError:
            pass

    return dest_folder / src.name


def record_search_files_context(mission: Mission, step: MissionStep, output: dict[str, Any]) -> None:
    """Persist verified search results for downstream logical steps."""
    pattern = str(output.get("pattern") or step.tool_arguments.get("pattern") or "*")
    matches = filter_search_matches_for_pattern(list(output.get("matches") or []), pattern)
    verified = {
        **output,
        "matches": matches,
        "count": len(matches),
        "verified": True,
    }
    record_step_tool_output(mission, step, verified)
    payload = {
        "matched_files": [str(item.get("path") or "") for item in matches if item.get("path")],
        "source_location": str(output.get("folder") or step.tool_arguments.get("path") or ""),
        "file_pattern": pattern,
        "result_count": len(matches),
    }
    mission.working_context.setdefault("search_results", {})[step.step_id] = payload
    mission.working_context["last_search"] = payload
    from hermes.mission.copy_audit import audit_copy_chain

    audit_copy_chain(
        "search_context_persisted",
        mission_id=mission.mission_id,
        step_id=step.step_id,
        verified=True,
        matched_files=payload["matched_files"],
        result_count=payload["result_count"],
        extra={"file_pattern": pattern, "source_location": payload["source_location"]},
    )


def search_empty_user_message(output: dict[str, Any]) -> str:
    from hermes.context.system_paths import folder_display_name

    folder_raw = str(output.get("folder") or output.get("source_location") or "")
    pattern = str(output.get("pattern") or output.get("file_pattern") or "")
    folder = Path(folder_raw) if folder_raw else None
    location = folder_display_name(folder) if folder is not None else "Kaynak klasor"
    if "pdf" in pattern.casefold():
        return f"{location} klasorunde PDF dosyasi bulamadim."
    return f"{location} klasorunde eslesen dosya bulamadim."


def search_result_summary(output: dict[str, Any]) -> str:
    count = int(output.get("count") or output.get("result_count") or 0)
    pattern = str(output.get("pattern") or output.get("file_pattern") or "*")
    if count <= 0:
        return search_empty_user_message(output)
    if "pdf" in pattern.casefold():
        return f"{count} PDF dosyasi bulundu"
    return f"{count} dosya bulundu"


def verify_copy_destinations(
    matches: list[dict[str, Any]],
    destination: str,
    *,
    pattern: str = "*.pdf",
    preserve_structure: bool = False,
    source_root: str | None = None,
) -> dict[str, Any]:
    """Filesystem verification for copy_search_matches — source of truth."""
    verified_files: list[str] = []
    missing: list[str] = []
    size_mismatch: list[str] = []
    invalid_dest: list[str] = []
    dest_root = resolve_destination_folder(destination)
    dest_root.mkdir(parents=True, exist_ok=True)

    for item in matches:
        if not isinstance(item, dict):
            continue
        source_raw = str(item.get("path") or "").strip()
        if not source_raw:
            continue
        dest_path = expected_copy_destination(
            source_raw,
            destination,
            preserve_structure=preserve_structure,
            source_root=source_root,
        )
        if pattern_requires_pdf(pattern) and not file_has_extension(dest_path.name, ".pdf"):
            invalid_dest.append(str(dest_path))
            continue
        src_path = Path(source_raw)
        if not dest_path.is_file():
            missing.append(str(dest_path))
            continue
        if src_path.is_file():
            try:
                if dest_path.stat().st_size != src_path.stat().st_size:
                    size_mismatch.append(str(dest_path))
                    continue
            except OSError:
                size_mismatch.append(str(dest_path))
                continue
        verified_files.append(str(dest_path))

    total = len([item for item in matches if isinstance(item, dict) and item.get("path")])
    ok = total > 0 and not missing and not size_mismatch and not invalid_dest and len(verified_files) == total
    return {
        "ok": ok,
        "total": total,
        "verified_count": len(verified_files),
        "verified_files": verified_files,
        "missing": missing,
        "size_mismatch": size_mismatch,
        "invalid_destination": invalid_dest,
        "destination": str(dest_root),
    }


async def run_copy_search_matches(
    matches: list[dict[str, Any]],
    destination: str,
    *,
    pattern: str = "*.pdf",
    preserve_structure: bool = False,
    source_root: str | None = None,
    mission_id: str | None = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    """
    Copy verified search matches to destination with idempotent skip, then filesystem verify.
    """
    import shutil

    from hermes.mission.copy_audit import audit_copy_chain
    from hermes.tools.windows.file_tools import resolve_user_path

    dest_root = resolve_destination_folder(destination)
    dest_root.mkdir(parents=True, exist_ok=True)

    audit_copy_chain(
        "run_copy_search_matches_input",
        mission_id=mission_id,
        step_id=step_id,
        destination=str(dest_root),
        extra={
            "input_count": len(matches),
            "pattern": pattern,
            "preserve_structure": preserve_structure,
        },
    )

    copy_matches, skipped_non_pdf = select_copy_matches(
        matches,
        pattern,
        mission_id=mission_id,
        step_id=step_id,
    )

    audit_copy_chain(
        "select_copy_matches_result",
        mission_id=mission_id,
        step_id=step_id,
        matched_files=[str(item.get("path") or "") for item in copy_matches],
        result_count=len(copy_matches),
        extra={"skipped_non_pdf": skipped_non_pdf[:10]},
    )

    copied = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    failed_files: list[str] = []

    for item in copy_matches:
        source_raw = str(item.get("path") or "").strip()
        if not source_raw:
            continue
        src_path = resolve_user_path(source_raw)
        if not src_path.is_file():
            failed += 1
            failed_files.append(source_raw)
            errors.append(f"Kaynak yok: {source_raw}")
            continue
        if pattern_requires_pdf(pattern) and not is_pdf_path(source_raw):
            skipped_non_pdf.append(source_raw)
            logger.info(
                "NON_PDF_MATCH_SKIPPED",
                path=source_raw,
                pattern=pattern,
                mission_id=mission_id,
                step_id=step_id,
            )
            continue
        dest_path = expected_copy_destination(
            source_raw,
            destination,
            preserve_structure=preserve_structure,
            source_root=source_root,
        )
        audit_copy_chain(
            "copy_executor_call",
            mission_id=mission_id,
            step_id=step_id,
            source=str(src_path),
            destination=str(dest_path),
            extension=Path(source_raw).suffix.casefold(),
        )
        try:
            if dest_path.is_file() and dest_path.stat().st_size == src_path.stat().st_size:
                skipped += 1
                continue
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)
            if dest_path.is_file() and (
                not pattern_requires_pdf(pattern) or is_pdf_path(str(dest_path))
            ):
                copied += 1
            else:
                failed += 1
                failed_files.append(source_raw)
                errors.append(f"Kopyalanamadi: {dest_path}")
        except OSError as exc:
            failed += 1
            failed_files.append(source_raw)
            errors.append(f"{source_raw}: {exc}")

    verify = verify_copy_destinations(
        copy_matches,
        destination,
        pattern=pattern,
        preserve_structure=preserve_structure,
        source_root=source_root,
    )
    verify["copied"] = copied
    verify["skipped"] = skipped
    verify["failed"] = failed
    verify["errors"] = errors
    verify["failed_files"] = failed_files
    verify["skipped_non_pdf"] = skipped_non_pdf
    verify["partial"] = verify["total"] > 0 and verify.get("verified_count", 0) < verify["total"]
    audit_copy_chain(
        "copy_verification_final",
        mission_id=mission_id,
        step_id=step_id,
        destination=str(verify.get("destination")),
        result_count=verify.get("verified_count"),
        verified=bool(verify.get("ok")),
        matched_files=list(verify.get("verified_files") or [])[:10],
        extra={
            "failed": verify.get("failed"),
            "partial": verify.get("partial"),
            "skipped_non_pdf": skipped_non_pdf[:10],
        },
    )
    return verify


def record_copy_search_context(mission: Mission, step: MissionStep, report: dict[str, Any]) -> None:
    mission.working_context.setdefault("copy_results", {})[step.step_id] = {
        "destination": report.get("destination"),
        "verified_files": list(report.get("verified_files") or []),
        "verified_count": report.get("verified_count", 0),
        "copied": report.get("copied", 0),
        "skipped": report.get("skipped", 0),
        "failed": report.get("failed", 0),
        "failed_files": list(report.get("failed_files") or []),
        "skipped_non_pdf": list(report.get("skipped_non_pdf") or []),
        "missing": list(report.get("missing") or []),
        "size_mismatch": list(report.get("size_mismatch") or []),
        "partial": bool(report.get("partial")),
    }
    record_step_tool_output(
        mission,
        step,
        {
            "destination": report.get("destination"),
            "verified_count": report.get("verified_count", 0),
            "copied": report.get("copied", 0),
            "skipped": report.get("skipped", 0),
            "failed": report.get("failed", 0),
            "verified_files": list(report.get("verified_files") or []),
            "filesystem_verified": bool(report.get("ok")),
            "partial": bool(report.get("partial")),
        },
    )


def copy_search_result_summary(report: dict[str, Any]) -> str:
    total = int(report.get("total") or 0)
    verified = int(report.get("verified_count") or 0)
    copied = int(report.get("copied") or 0)
    skipped = int(report.get("skipped") or 0)
    failed = int(report.get("failed") or 0)
    skipped_non_pdf = len(list(report.get("skipped_non_pdf") or []))

    if verified <= 0 and failed <= 0:
        return "Dosyalar kopyalanamadi"
    if failed > 0 or report.get("partial"):
        return f"{verified}/{total} PDF dogrulandi, {failed} basarisiz"
    parts = [f"{verified} dosya dogrulandi"]
    if copied:
        parts.append(f"{copied} kopyalandi")
    if skipped:
        parts.append(f"{skipped} zaten vardi")
    if skipped_non_pdf:
        parts.append(f"{skipped_non_pdf} pdf disi atlandi")
    return ", ".join(parts)


def get_step_tool_output(mission: Mission, step_id: str) -> dict[str, Any] | None:
    entry = (mission.working_context.get("step_outputs") or {}).get(step_id)
    if isinstance(entry, dict):
        output = entry.get("output")
        if isinstance(output, dict):
            return output

    step = next((item for item in mission.steps if item.step_id == step_id), None)
    if step is not None:
        persisted = step.metadata.get("tool_output")
        if isinstance(persisted, dict):
            return persisted
    return None


def get_prepared_content(mission: Mission, step_id: str) -> str | None:
    step = next((item for item in mission.steps if item.step_id == step_id), None)
    if step and step.metadata.get("prepared_content"):
        return str(step.metadata["prepared_content"])
    prepared = (mission.working_context.get("prepared_contents") or {}).get(step_id)
    return str(prepared) if prepared else None


def resolve_verified_search_matches(
    mission: Mission,
    step: MissionStep,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """
    Canonical copy source: VERIFIED search_files matched_files only.

    Returns (matches, pattern, search_step_id).
    """
    from hermes.mission.copy_audit import audit_copy_chain

    steps_by_id = {item.step_id: item for item in mission.steps}
    search_ids: list[str] = []
    for dep_id in step.depends_on:
        dep = steps_by_id.get(dep_id)
        if dep is not None and dep.tool_name == "search_files":
            search_ids.append(dep_id)
    if not search_ids:
        for dep_id in dependency_closure(step, mission):
            if dep_id == step.step_id:
                continue
            dep = steps_by_id.get(dep_id)
            if dep is not None and dep.tool_name == "search_files" and dep_id not in search_ids:
                search_ids.append(dep_id)

    for search_step_id in search_ids:
        output = get_step_tool_output(mission, search_step_id)
        search_payload = (mission.working_context.get("search_results") or {}).get(search_step_id)
        dep_step = steps_by_id.get(search_step_id)
        pattern = str(
            (output or {}).get("pattern")
            or (output or {}).get("file_pattern")
            or (search_payload or {}).get("file_pattern")
            or (dep_step.tool_arguments.get("pattern") if dep_step is not None else "")
            or "*.pdf"
        )

        if not isinstance(output, dict) or output.get("verified") is not True:
            if isinstance(search_payload, dict):
                matched_files = [
                    str(item).strip()
                    for item in (search_payload.get("matched_files") or [])
                    if str(item).strip()
                ]
                matches = [
                    {"path": path, "name": Path(path).name}
                    for path in matched_files
                ]
                matches = filter_search_matches_for_pattern(matches, pattern)
                if pattern_requires_pdf(pattern):
                    matches = [item for item in matches if is_pdf_match(item)]
                audit_copy_chain(
                    "verified_search_matches_resolved",
                    mission_id=mission.mission_id,
                    step_id=step.step_id,
                    depends_on=list(step.depends_on),
                    verified=True,
                    matched_files=[str(item.get("path") or "") for item in matches],
                    result_count=len(matches),
                    extra={
                        "search_step_id": search_step_id,
                        "pattern": pattern,
                        "source": "search_results_fallback",
                    },
                )
                return matches, pattern, search_step_id
            audit_copy_chain(
                "verified_search_rejected",
                mission_id=mission.mission_id,
                step_id=step.step_id,
                depends_on=list(step.depends_on),
                verified=False,
                extra={"search_step_id": search_step_id, "reason": "not_verified"},
            )
            continue

        matched_files: list[str] = []
        if isinstance(search_payload, dict):
            matched_files = [
                str(item).strip()
                for item in (search_payload.get("matched_files") or [])
                if str(item).strip()
            ]
        if not matched_files:
            matched_files = [
                str(item.get("path") or "").strip()
                for item in (output.get("matches") or [])
                if isinstance(item, dict) and str(item.get("path") or "").strip()
            ]

        matches = [
            {"path": path, "name": Path(path).name}
            for path in matched_files
        ]
        matches = filter_search_matches_for_pattern(matches, pattern)
        if pattern_requires_pdf(pattern):
            matches = [item for item in matches if is_pdf_match(item)]

        audit_copy_chain(
            "verified_search_matches_resolved",
            mission_id=mission.mission_id,
            step_id=step.step_id,
            depends_on=list(step.depends_on),
            verified=True,
            matched_files=[str(item.get("path") or "") for item in matches],
            result_count=len(matches),
            extra={"search_step_id": search_step_id, "pattern": pattern},
        )
        return matches, pattern, search_step_id

    audit_copy_chain(
        "verified_search_missing",
        mission_id=mission.mission_id,
        step_id=step.step_id,
        depends_on=list(step.depends_on),
        verified=False,
    )
    return [], "*.pdf", None


def find_verified_search_output(
    mission: Mission,
    step: MissionStep,
) -> dict[str, Any] | None:
    """Return only VERIFIED search_files output from explicit dependencies."""
    steps_by_id = {item.step_id: item for item in mission.steps}
    search_ids: list[str] = []
    for dep_id in step.depends_on:
        dep = steps_by_id.get(dep_id)
        if dep is not None and dep.tool_name == "search_files":
            search_ids.append(dep_id)
    if not search_ids:
        for dep_id in dependency_closure(step, mission):
            if dep_id == step.step_id:
                continue
            dep = steps_by_id.get(dep_id)
            if dep is not None and dep.tool_name == "search_files" and dep_id not in search_ids:
                search_ids.append(dep_id)
    for dep_id in search_ids:
        output = get_step_tool_output(mission, dep_id)
        if not isinstance(output, dict):
            continue
        if output.get("verified") is not True:
            continue
        if not isinstance(output.get("matches"), list):
            continue
        return output
    return None


def find_dependency_output(
    mission: Mission,
    step: MissionStep,
    *,
    tool_name: str | None = None,
) -> dict[str, Any] | None:
    if tool_name == "search_files":
        return find_verified_search_output(mission, step)
    steps_by_id = {item.step_id: item for item in mission.steps}
    for dep_id in dependency_closure(step, mission):
        if dep_id == step.step_id:
            continue
        dep = steps_by_id.get(dep_id)
        if dep is None:
            continue
        if tool_name and dep.tool_name != tool_name:
            continue
        output = get_step_tool_output(mission, dep.step_id)
        if output:
            return output
    return None


def resolve_argument_binding(mission: Mission, binding: dict[str, str]) -> str | None:
    source_step_id = str(binding.get("source_step_id") or "").strip()
    source_field = str(binding.get("source_field") or "").strip()
    if not source_step_id or not source_field:
        return None

    if source_field == SOURCE_FIELD_PREPARED_CONTENT:
        prepared = get_prepared_content(mission, source_step_id)
        return prepared if prepared else None

    if source_field == SOURCE_FIELD_TOOL_OUTPUT_FORMATTED:
        output = get_step_tool_output(mission, source_step_id)
        if output:
            return format_system_info_file_content(output)
        return None

    if source_field == SOURCE_FIELD_TOOL_DESTINATION:
        output = get_step_tool_output(mission, source_step_id)
        if isinstance(output, dict):
            dest = output.get("destination") or output.get("path")
            if dest:
                return str(dest)
        return None

    return None


def resolve_write_file_content(step: MissionStep, mission: Mission) -> str | None:
    for binding in step.argument_bindings:
        if str(binding.get("argument") or "") != "content":
            continue
        resolved = resolve_argument_binding(mission, binding)
        if resolved and not is_placeholder_content(resolved):
            return resolved

    content_from = step.metadata.get("content_from_step")
    if content_from:
        prepared = get_prepared_content(mission, str(content_from))
        if prepared and not is_placeholder_content(prepared):
            return prepared

    steps_by_id = {item.step_id: item for item in mission.steps}
    for dep_id in reversed(dependency_closure(step, mission)):
        dep = steps_by_id.get(dep_id)
        if dep is None:
            continue
        prepared = dep.metadata.get("prepared_content")
        if prepared and not is_placeholder_content(str(prepared)):
            return str(prepared)
        if dep.tool_name == "get_system_info" and dep.status == MissionStepStatus.COMPLETED:
            output = get_step_tool_output(mission, dep.step_id)
            if output:
                formatted = format_system_info_file_content(output)
                if not is_placeholder_content(formatted):
                    return formatted
    return None


def resolve_search_matches_fallback(
    mission: Mission,
    *,
    preferred_step_ids: tuple[str, ...] = ("scan_pdfs", "search_source_files"),
) -> tuple[list[dict[str, Any]], str, str | None]:
    """Use persisted search_results when verified step output is unavailable."""
    pattern = "*.pdf"
    for step_id in preferred_step_ids:
        bucket = (mission.working_context.get("search_results") or {}).get(step_id)
        if not isinstance(bucket, dict):
            continue
        matched_files = [
            str(item).strip()
            for item in (bucket.get("matched_files") or [])
            if str(item).strip()
        ]
        if not matched_files:
            continue
        pat = str(bucket.get("file_pattern") or pattern)
        matches = [{"path": path, "name": Path(path).name} for path in matched_files]
        return filter_search_matches_for_pattern(matches, pat), pat, step_id

    last_search = mission.working_context.get("last_search")
    if isinstance(last_search, dict):
        matched_files = [
            str(item).strip()
            for item in (last_search.get("matched_files") or [])
            if str(item).strip()
        ]
        if matched_files:
            pat = str(last_search.get("file_pattern") or pattern)
            matches = [{"path": path, "name": Path(path).name} for path in matched_files]
            return filter_search_matches_for_pattern(matches, pat), pat, None

    for step_id in preferred_step_ids:
        output = get_step_tool_output(mission, step_id)
        if not isinstance(output, dict):
            continue
        raw_matches = output.get("matches") or []
        if not isinstance(raw_matches, list) or not raw_matches:
            continue
        pat = str(output.get("pattern") or pattern)
        normalized = [
            item if isinstance(item, dict) else {"path": str(item), "name": Path(str(item)).name}
            for item in raw_matches
        ]
        return filter_search_matches_for_pattern(normalized, pat), pat, step_id

    return [], pattern, None


def resolve_mission_browser_url(mission: Mission, step: MissionStep | None = None) -> str:
    """Resolve browser page URL from mission working context and completed steps."""
    browser_page = mission.working_context.get("browser_page_content") or {}
    if isinstance(browser_page, dict):
        url = str(browser_page.get("url") or "").strip()
        if url:
            return url

    for key in ("last_browser_url", "last_url"):
        url = str(mission.working_context.get(key) or "").strip()
        if url:
            return url

    refs = mission.working_context.get("resolved_references")
    if isinstance(refs, dict):
        for key in ("last_url", "last_browser_url", "url"):
            url = str(refs.get(key) or "").strip()
            if url:
                return url

    snapshot = mission.working_context.get("context_snapshot") or getattr(mission, "context_snapshot", None) or {}
    if isinstance(snapshot, dict):
        for key in ("last_browser_url", "last_url"):
            url = str(snapshot.get(key) or "").strip()
            if url:
                return url

    preferred_steps = ("navigate_target", "open_url")
    steps_by_id = {item.step_id: item for item in mission.steps}
    for step_id in preferred_steps:
        dep = steps_by_id.get(step_id)
        if dep is not None and dep.tool_name == "open_url":
            output = get_step_tool_output(mission, step_id)
            if isinstance(output, dict):
                url = str(output.get("url") or "").strip()
                if url:
                    return url
            url = str(dep.tool_arguments.get("url") or "").strip()
            if url:
                return url

    for dep in mission.steps:
        if dep.tool_name != "open_url":
            continue
        output = get_step_tool_output(mission, dep.step_id)
        if isinstance(output, dict):
            url = str(output.get("url") or "").strip()
            if url:
                return url
        url = str(dep.tool_arguments.get("url") or "").strip()
        if url:
            return url

    del step
    return ""


def resolve_step_tool_arguments(step: MissionStep, mission: Mission) -> dict[str, Any]:
    args = dict(step.tool_arguments or {})

    for binding in step.argument_bindings or []:
        arg_name = str(binding.get("argument") or "").strip()
        if not arg_name:
            continue
        resolved = resolve_argument_binding(mission, binding)
        if resolved is not None and resolved != "":
            args[arg_name] = resolved

    if step.tool_name == "write_file":
        if not is_placeholder_content(str(args.get("content") or "")):
            if step.metadata.get("unique_if_exists") or step.step_id == "write_report_file":
                args["unique_if_exists"] = True
            return args
        resolved_content = resolve_write_file_content(step, mission)
        if resolved_content:
            args["content"] = resolved_content
        if step.metadata.get("unique_if_exists") or step.step_id == "write_report_file":
            args["unique_if_exists"] = True
    if step.tool_name == "read_screen_text":
        strategy = str(
            mission.working_context.get("NEW_STRATEGY")
            or mission.working_context.get("last_replan", {}).get("NEW_STRATEGY")
            or ""
        ).strip()
        if strategy:
            args["strategy"] = strategy
        args.setdefault("focus_browser", True)
        args.setdefault("app", "chrome")
        title_hint = str(
            args.get("title_hint")
            or mission.working_context.get("browser_title_hint")
            or ""
        ).strip()
        if not title_hint:
            refs = mission.working_context.get("resolved_references")
            if isinstance(refs, dict):
                for key in ("last_url", "url", "last_opened_url"):
                    url = str(refs.get(key) or "").strip()
                    if url:
                        from urllib.parse import urlparse

                        host = urlparse(url).netloc.replace("www.", "")
                        if host:
                            title_hint = host.split(".")[0]
                            break
                if not title_hint:
                    window = str(refs.get("last_browser_window") or refs.get("browser_window") or "").strip()
                    if window:
                        title_hint = window.split("-")[0].strip()
        if title_hint:
            args["title_hint"] = title_hint
    return args


def extension_destination_map(source_root: str) -> dict[str, str]:
    root = Path(source_root)
    return {
        ".pdf": str(root / "PDF"),
        ".txt": str(root / "Metin"),
        ".md": str(root / "Metin"),
        ".csv": str(root / "Metin"),
        ".doc": str(root / "Metin"),
        ".docx": str(root / "Metin"),
    }


async def run_organize_files_by_type(
    matches: list[dict[str, Any]],
    source_root: str,
    *,
    mission_id: str | None = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    """Move desktop files into type folders; verify destination before removing source."""
    from hermes.mission.reality_verification import safe_move_file
    from hermes.tools.windows.file_tools import resolve_user_path

    dest_map = extension_destination_map(source_root)
    for folder in dest_map.values():
        Path(folder).mkdir(parents=True, exist_ok=True)

    moved = 0
    skipped = 0
    failed = 0
    mutations: list[dict[str, Any]] = []
    verified_files: list[str] = []

    for item in matches:
        if not isinstance(item, dict):
            continue
        source_raw = str(item.get("path") or "").strip()
        if not source_raw:
            continue
        src = resolve_user_path(source_raw)
        if not src.is_file():
            continue
        ext = src.suffix.casefold()
        dest_folder = dest_map.get(ext)
        if not dest_folder:
            skipped += 1
            continue
        dest_path = Path(dest_folder) / src.name
        result = safe_move_file(src, dest_path)
        mutations.append(
            {
                "intent": "MOVE",
                "source": str(src.resolve()) if src.is_file() else source_raw,
                "destination": str(dest_path.resolve()),
                "verified": result.get("ok"),
                "reason": result.get("reason"),
            }
        )
        if result.get("ok"):
            moved += 1
            verified_files.append(str(dest_path.resolve()))
        elif result.get("reason") == "source_missing" and dest_path.is_file():
            skipped += 1
            verified_files.append(str(dest_path.resolve()))
        else:
            failed += 1

    ok = moved > 0 and failed == 0
    return {
        "ok": ok,
        "moved": moved,
        "copied": moved,
        "skipped": skipped,
        "failed": failed,
        "verified_files": verified_files,
        "mutations": mutations,
        "mission_id": mission_id,
        "step_id": step_id,
    }


async def run_copy_classified_pdfs(
    matches: list[dict[str, Any]],
    destination: str,
    *,
    mission_id: str | None = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    """Move only PDF paths explicitly marked important after content classification."""
    from hermes.mission.reality_verification import safe_move_file
    from hermes.tools.windows.file_tools import resolve_user_path

    dest_root = resolve_destination_folder(destination)
    dest_root.mkdir(parents=True, exist_ok=True)
    important = [item for item in matches if isinstance(item, dict) and item.get("important")]
    if not important:
        return {
            "ok": True,
            "moved": 0,
            "copied": 0,
            "failed": 0,
            "partial": True,
            "verified_files": [],
            "mutations": [],
            "mission_id": mission_id,
            "step_id": step_id,
        }

    moved = 0
    skipped = 0
    failed = 0
    mutations: list[dict[str, Any]] = []
    verified_files: list[str] = []

    for item in important:
        source_raw = str(item.get("path") or "").strip()
        if not source_raw:
            continue
        src = resolve_user_path(source_raw)
        if src.suffix.casefold() != ".pdf":
            continue
        dest_path = dest_root / src.name
        try:
            if src.resolve().is_relative_to(dest_root.resolve()):
                skipped += 1
                verified_files.append(str(src.resolve()))
                mutations.append(
                    {
                        "intent": "MOVE",
                        "source": source_raw,
                        "destination": str(dest_path.resolve()),
                        "verified": True,
                        "reason": "already_at_destination",
                    }
                )
                continue
        except (ValueError, OSError):
            pass
        if not src.is_file():
            if dest_path.is_file():
                skipped += 1
                verified_files.append(str(dest_path.resolve()))
                mutations.append(
                    {
                        "intent": "MOVE",
                        "source": source_raw,
                        "destination": str(dest_path.resolve()),
                        "verified": True,
                        "reason": "already_at_destination",
                    }
                )
            continue
        result = safe_move_file(src, dest_path)
        mutations.append(
            {
                "intent": "MOVE",
                "source": source_raw,
                "destination": str(dest_path.resolve()),
                "verified": result.get("ok"),
                "reason": result.get("reason"),
            }
        )
        if result.get("ok"):
            moved += 1
            verified_files.append(str(dest_path.resolve()))
        elif result.get("reason") == "source_missing" and dest_path.is_file():
            skipped += 1
            verified_files.append(str(dest_path.resolve()))
        else:
            failed += 1

    ok = bool(moved > 0 or skipped > 0 or not important)
    partial = bool(failed > 0 and (moved > 0 or skipped > 0))
    return {
        "ok": ok,
        "moved": moved + skipped,
        "copied": moved + skipped,
        "skipped": skipped,
        "failed": failed,
        "partial": partial,
        "verified_files": verified_files,
        "mutations": mutations,
        "mission_id": mission_id,
        "step_id": step_id,
    }
