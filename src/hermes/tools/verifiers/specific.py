from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hermes.tools.verifiers.base import BaseVerifier, Observation, VerificationResult, VerificationStatus
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.windows.file_tools import resolve_user_path
from hermes.utils.logging import get_logger

_logger = get_logger(__name__)


def _resolve_folder_path(raw: str) -> Path:
    text = (raw or "").strip()
    if not text:
        raise ValueError("path required")
    target = Path(text).expanduser()
    if not target.is_absolute():
        target = Path.home() / "Desktop" / target
    return target.resolve()


def _resolve_git_target(ctx: VerifierContext) -> Path | None:
    output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
    raw = str(output.get("path") or ctx.tool_arguments.get("target_dir") or "").strip()
    if not raw:
        url = str(ctx.tool_arguments.get("repo_url") or ctx.tool_arguments.get("url") or "")
        repo_name = Path(urlparse(url.replace("git@", "https://")).path).stem or "repo"
        if repo_name:
            return (Path.home() / "Desktop" / repo_name).resolve()
        return None
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = Path.home() / "Desktop" / target
    return target.resolve()


def _expected_dns_servers(ctx: VerifierContext) -> list[str]:
    preset = str(ctx.tool_arguments.get("preset") or "").strip().lower()
    presets = {
        "google": ["8.8.8.8", "8.8.4.4"],
        "cloudflare": ["1.1.1.1", "1.0.0.1"],
        "quad9": ["9.9.9.9", "149.112.112.112"],
        "dhcp": [],
    }
    if preset in presets:
        return list(presets[preset])
    servers = ctx.tool_arguments.get("servers")
    if isinstance(servers, str):
        return [part.strip() for part in servers.replace(",", " ").split() if part.strip()]
    if isinstance(servers, list):
        return [str(item).strip() for item in servers if str(item).strip()]
    output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
    from_output = output.get("servers")
    if isinstance(from_output, list):
        return [str(item).strip() for item in from_output if str(item).strip()]
    return []


def _dns_from_network_config(data: Any) -> list[str]:
    servers: list[str] = []
    if not isinstance(data, dict):
        return servers
    dns_block = data.get("dns_servers")
    if isinstance(dns_block, list):
        for item in dns_block:
            if isinstance(item, dict):
                addresses = item.get("ServerAddresses") or item.get("ServerAddresses".lower())
                if isinstance(addresses, list):
                    servers.extend(str(a).strip() for a in addresses if str(a).strip())
                elif isinstance(addresses, str):
                    servers.extend(part.strip() for part in addresses.split(",") if part.strip())
    adapters = data.get("adapters")
    if isinstance(adapters, list):
        for adapter in adapters:
            if not isinstance(adapter, dict):
                continue
            dns_value = adapter.get("DNS") or adapter.get("dns")
            if isinstance(dns_value, str) and dns_value.strip():
                servers.extend(part.strip() for part in dns_value.split(",") if part.strip())
    return servers


class GitCloneVerifier(BaseVerifier):
    tool_names = ("git_clone",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="git_clone_filesystem",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        try:
            target = _resolve_git_target(ctx)
        except (ValueError, OSError):
            target = None
        if target is None:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="git_clone_filesystem",
                details={"reason": "target_path_unknown"},
            )
        observation = Observation(
            source="filesystem",
            data={
                "path": str(target),
                "exists": target.exists(),
                "git_dir_exists": (target / ".git").exists(),
                "entries": [item.name for item in target.iterdir()] if target.is_dir() else [],
            },
        )
        if not target.is_dir():
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="git_clone_filesystem",
                details={"reason": "target_directory_missing", "path": str(target)},
                observation=observation,
            )
        if not (target / ".git").exists():
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="git_clone_filesystem",
                details={"reason": "git_metadata_missing", "path": str(target)},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            method="git_clone_filesystem",
            details={"path": str(target), "git_dir": str(target / ".git")},
            observation=observation,
        )


class InstallProgramVerifier(BaseVerifier):
    tool_names = ("install_program",)
    default_timeout = 30.0

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="install_program_state",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        package = str(ctx.tool_arguments.get("package") or "").strip()
        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        observation_data: dict[str, Any] = {"package": package, "execution_output": output}

        if output.get("note") and "kurulu" in str(output.get("note")).casefold():
            observation = Observation(source="execution_output", data=observation_data)
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                method="install_program_idempotent",
                details={"reason": "already_installed", "package": package},
                observation=observation,
            )

        if ctx.observe_tool and package:
            observed = await ctx.observe_tool(
                "list_installed_programs",
                {"filter_name": package, "limit": 20},
                ctx.run_id,
            )
            if observed and observed.success and isinstance(observed.output, dict):
                programs = observed.output.get("programs") or []
                names = [
                    str(item.get("DisplayName", ""))
                    for item in programs
                    if isinstance(item, dict)
                ]
                observation_data["installed_programs"] = names
                observation_data["program_count"] = observed.output.get("count")
                matched = any(package.casefold() in name.casefold() for name in names if name)
                if matched:
                    return VerificationResult(
                        status=VerificationStatus.VERIFIED,
                        method="list_installed_programs",
                        details={"package": package, "matched_names": names[:5]},
                        observation=Observation(source="list_installed_programs", data=observation_data),
                    )
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    method="list_installed_programs",
                    details={"reason": "program_not_found", "package": package, "candidates": names[:5]},
                    observation=Observation(source="list_installed_programs", data=observation_data),
                )

        if output.get("verified") is True:
            observation = Observation(source="execution_output", data=observation_data)
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                method="install_program_winget_output",
                details={"package": package, "winget_id": output.get("winget_id")},
                observation=observation,
            )

        return VerificationResult(
            status=VerificationStatus.UNKNOWN,
            method="install_program_state",
            details={"reason": "cannot_confirm_installation", "package": package},
            observation=Observation(source="execution_output", data=observation_data),
        )


class CreateFolderVerifier(BaseVerifier):
    tool_names = ("create_folder",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="create_folder_filesystem",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        raw = str(output.get("path") or ctx.tool_arguments.get("path") or ctx.tool_arguments.get("name") or "")
        if not raw.strip():
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="create_folder_filesystem",
                details={"reason": "path_missing"},
            )
        try:
            target = _resolve_folder_path(raw)
        except ValueError:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="create_folder_filesystem",
                details={"reason": "path_unresolvable"},
            )
        observation = Observation(
            source="filesystem",
            data={"path": str(target), "exists": target.exists(), "is_dir": target.is_dir()},
        )
        if target.is_dir():
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                method="create_folder_filesystem",
                details={"path": str(target)},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.FAILED,
            method="create_folder_filesystem",
            details={"reason": "folder_missing", "path": str(target)},
            observation=observation,
        )


class GetSystemInfoVerifier(BaseVerifier):
    tool_names = ("get_system_info",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="get_system_info_output",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        from hermes.mission.step_context import missing_system_info_fields, system_info_output_valid

        missing = missing_system_info_fields(output)
        observation = Observation(
            source="execution_output",
            data={"keys": list(output.keys())[:20], "missing_fields": missing},
        )
        method = ctx.verification_method or "system_info_fields"
        if method == "system_info_fields" and not system_info_output_valid(output):
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="get_system_info_fields",
                details={"reason": "missing_required_fields", "missing": missing},
                observation=observation,
            )
        if output:
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                method="get_system_info_output",
                details={"reason": "output_present"},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.FAILED,
            method="get_system_info_output",
            details={"reason": "output_missing"},
            observation=observation,
        )


class ReadFileVerifier(BaseVerifier):
    tool_names = ("read_file",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        raw = str(
            ctx.tool_arguments.get("path")
            or ctx.tool_arguments.get("file")
            or ""
        ).strip()
        if isinstance(ctx.execution_output, dict):
            actual = str(ctx.execution_output.get("path") or "").strip()
            if actual:
                raw = actual
        if not raw:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="read_file_filesystem",
                details={"reason": "path_missing"},
            )
        try:
            target = resolve_user_path(raw)
        except Exception as exc:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="read_file_filesystem",
                details={"reason": "path_unresolved", "error": str(exc)},
            )
        observation = Observation(
            source="filesystem",
            data={"path": str(target), "exists": target.exists(), "is_file": target.is_file()},
        )
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="read_file_filesystem",
                details={"reason": "execution_failed", "error": ctx.execution_error},
                observation=observation,
            )
        if target.is_file():
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                method="read_file_filesystem",
                details={"path": str(target), "size": target.stat().st_size},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.FAILED,
            method="read_file_filesystem",
            details={"reason": "not_a_file", "path": str(target)},
            observation=observation,
        )


class OpenPathVerifier(BaseVerifier):
    tool_names = ("open_path",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        raw = str(ctx.tool_arguments.get("path") or ctx.tool_arguments.get("target") or "").strip()
        if isinstance(ctx.execution_output, dict):
            actual = str(ctx.execution_output.get("path") or "").strip()
            if actual:
                raw = actual
        if not raw:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="open_path_filesystem",
                details={"reason": "path_missing"},
            )
        try:
            target = Path(raw).expanduser()
            if not target.is_absolute():
                target = Path.home() / "Desktop" / target
            target = target.resolve()
        except Exception as exc:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="open_path_filesystem",
                details={"reason": "path_unresolved", "error": str(exc)},
            )
        exists = target.exists()
        observation = Observation(
            source="filesystem",
            data={"path": str(target), "exists": exists, "is_dir": target.is_dir()},
        )
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="open_path_filesystem",
                details={"reason": "execution_failed", "error": ctx.execution_error},
                observation=observation,
            )
        if exists:
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                method="open_path_filesystem",
                details={"path": str(target)},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.FAILED,
            method="open_path_filesystem",
            details={"reason": "path_missing_on_disk", "path": str(target)},
            observation=observation,
        )


class OpenAppVerifier(BaseVerifier):
    tool_names = ("open_app",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        raw_path = str(output.get("path") or "").strip()
        app = str(ctx.tool_arguments.get("app") or output.get("app") or "").strip()
        binary = Path(raw_path) if raw_path else None
        if binary is None or not binary.is_file():
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="open_app_process",
                details={"reason": "app_binary_missing", "app": app, "path": raw_path},
            )
        observation = Observation(
            source="process",
            data={"app": app, "path": str(binary), "pid": output.get("pid")},
        )
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="open_app_process",
                details={"reason": "execution_failed", "error": ctx.execution_error},
                observation=observation,
            )
        pid = output.get("pid")
        if pid not in (None, ""):
            try:
                import psutil

                if psutil.pid_exists(int(pid)):
                    return VerificationResult(
                        status=VerificationStatus.VERIFIED,
                        method="open_app_process",
                        details={"pid": int(pid), "path": str(binary)},
                        observation=observation,
                    )
            except (TypeError, ValueError, ImportError):
                pass
        try:
            from hermes.tools.windows.input_backend import find_app_window_title

            title = find_app_window_title(app) if app else None
        except Exception:
            title = None
        if title:
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                method="open_app_window",
                details={"window_title": title, "path": str(binary)},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.UNKNOWN,
            method="open_app_process",
            details={"reason": "process_unconfirmed", "path": str(binary)},
            observation=observation,
        )


class WriteFileVerifier(BaseVerifier):
    tool_names = ("write_file", "create_word_document")

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="write_file_filesystem",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        raw = str(ctx.tool_arguments.get("path") or ctx.tool_arguments.get("file") or "").strip()
        planned_path = raw
        if isinstance(ctx.execution_output, dict):
            actual = str(ctx.execution_output.get("path") or "").strip()
            if actual:
                raw = actual
        if not raw:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="write_file_filesystem",
                details={"reason": "path_missing"},
            )
        try:
            target = resolve_user_path(raw)
        except ValueError:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="write_file_filesystem",
                details={"reason": "path_unresolvable"},
            )
        exists = target.is_file()
        suffix = target.suffix.casefold()
        if exists and suffix in {".pdf", ".docx"}:
            from hermes.mission.reality_verification import verify_docx_document, verify_pdf_document

            expected = str(ctx.tool_arguments.get("content") or "").strip()
            needle = expected[:40] if expected and "\n" not in expected[:40] else ""
            check = (
                verify_pdf_document(target, expected_text=needle)
                if suffix == ".pdf"
                else verify_docx_document(target, expected_text="")
            )
            observation = Observation(
                source="filesystem",
                data={"path": str(target), "format_check": check},
            )
            if not check.get("ok"):
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    method="document_format",
                    details={"reason": check.get("reason"), "path": str(target)},
                    observation=observation,
                )
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                method="document_format",
                details={"path": str(target), "pages": check.get("pages")},
                observation=observation,
            )
        content = ctx.tool_arguments.get("content")
        content_hash: str | None = None
        hash_match: bool | None = None
        parent_mismatch = False
        planned_normalized = planned_path.replace("\\", "/")
        if exists and planned_path and ("/" in planned_normalized or "\\" in planned_path):
            parts = Path(planned_normalized).parts
            if len(parts) >= 2:
                expected_parent_name = parts[-2]
                parent_mismatch = target.parent.name.casefold() != expected_parent_name.casefold()
        if exists and isinstance(content, str):
            try:
                actual = target.read_text(encoding="utf-8")
                expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                actual_hash = hashlib.sha256(actual.encode("utf-8")).hexdigest()
                content_hash = actual_hash
                hash_match = expected_hash == actual_hash
            except OSError:
                hash_match = None
        observation = Observation(
            source="filesystem",
            data={
                "path": str(target),
                "planned_path": planned_path,
                "actual_write_path": str(target),
                "exists": exists,
                "size": target.stat().st_size if exists else None,
                "content_hash": content_hash,
                "hash_match": hash_match,
                "parent_directory": str(target.parent),
                "parent_mismatch": parent_mismatch,
            },
        )
        if not exists:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="write_file_filesystem",
                details={"reason": "file_missing", "path": str(target)},
                observation=observation,
            )
        if parent_mismatch:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="write_file_filesystem",
                details={
                    "reason": "unexpected_parent_directory",
                    "path": str(target),
                    "parent": str(target.parent),
                },
                observation=observation,
            )
        if hash_match is False:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="write_file_content_hash",
                details={"reason": "content_mismatch", "path": str(target)},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            method="write_file_filesystem" if hash_match is None else "write_file_content_hash",
            details={"path": str(target), "size": observation.data.get("size")},
            observation=observation,
        )


class SetDnsVerifier(BaseVerifier):
    tool_names = ("set_dns",)
    default_timeout = 20.0

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="set_dns_network_state",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        expected = _expected_dns_servers(ctx)
        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        adapter = str(output.get("adapter") or ctx.tool_arguments.get("adapter") or "").strip()

        observation_data: dict[str, Any] = {
            "expected_servers": expected,
            "adapter": adapter or None,
            "execution_output": output,
        }
        observed_servers: list[str] = []

        if ctx.observe_tool:
            observed = await ctx.observe_tool("get_network_config", {}, ctx.run_id)
            if observed and observed.success:
                observation_data["network_config"] = observed.output
                observed_servers = _dns_from_network_config(observed.output)

        if not observed_servers and isinstance(output.get("verified"), list):
            observed_servers = [str(item).strip() for item in output["verified"] if str(item).strip()]

        observation_data["observed_servers"] = observed_servers
        observation = Observation(
            source="get_network_config" if ctx.observe_tool else "execution_output",
            data=observation_data,
        )

        if not expected:
            if observed_servers == [] or "dhcp" in str(output.get("servers", "")).casefold():
                return VerificationResult(
                    status=VerificationStatus.VERIFIED,
                    method="set_dns_dhcp",
                    details={"adapter": adapter, "mode": "dhcp"},
                    observation=observation,
                )
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="set_dns_network_state",
                details={"reason": "dhcp_state_uncertain"},
                observation=observation,
            )

        if not observed_servers:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="set_dns_network_state",
                details={"reason": "dns_observation_unavailable", "expected": expected},
                observation=observation,
            )

        missing = [ip for ip in expected if ip not in observed_servers]
        if missing:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="set_dns_network_state",
                details={
                    "reason": "dns_mismatch",
                    "expected": expected,
                    "observed": observed_servers,
                    "missing": missing,
                },
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            method="set_dns_network_state",
            details={"expected": expected, "observed": observed_servers, "adapter": adapter},
            observation=observation,
        )


class ReadScreenTextVerifier(BaseVerifier):
    tool_names = ("read_screen_text",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        from hermes.mission.reality_verification import normalize_visible_page_text

        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="screen_text_substantive",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        text, error = normalize_visible_page_text(output)
        observation = Observation(source="execution_output", data={"text_len": len(text or ""), "error": error})
        if not text:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="screen_text_substantive",
                details={"reason": "not_substantive", "error": error or "empty_text"},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            method="screen_text_substantive",
            details={"text_len": len(text), "verified_output": {**output, "text": text, "content_type": "screen_text"}},
            observation=observation,
        )


class OpenUrlVerifier(BaseVerifier):
    tool_names = ("open_url", "browser_nav")

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="open_url_state",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        expected_url = str(ctx.tool_arguments.get("url") or "").strip()
        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        opened_url = str(output.get("url") or output.get("opened_url") or expected_url).strip()
        observation = Observation(
            source="execution_output",
            data={"expected_url": expected_url, "opened_url": opened_url, "raw_output": output},
        )
        if not expected_url:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="open_url_state",
                details={"reason": "expected_url_missing"},
                observation=observation,
            )
        normalized_expected = expected_url.rstrip("/").casefold()
        normalized_opened = opened_url.rstrip("/").casefold()
        if normalized_opened and (
            normalized_expected in normalized_opened or normalized_opened in normalized_expected
        ):
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                method="open_url_output",
                details={"url": opened_url or expected_url},
                observation=observation,
            )
        if output.get("opened") is True or output.get("success") is True:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="open_url_state",
                details={"reason": "browser_url_unconfirmed", "expected_url": expected_url},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.FAILED,
            method="open_url_state",
            details={"reason": "url_not_opened", "expected_url": expected_url},
            observation=observation,
        )


class SearchFilesVerifier(BaseVerifier):
    tool_names = ("search_files",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="search_files_filesystem",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )

        from hermes.mission.step_context import (
            filter_search_matches_for_pattern,
            scan_files_on_filesystem,
        )

        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        raw_path = str(output.get("folder") or ctx.tool_arguments.get("path") or "").strip()
        pattern = str(output.get("pattern") or ctx.tool_arguments.get("pattern") or "*").strip()
        hours = ctx.tool_arguments.get("modified_within_hours")
        if hours is None and isinstance(output.get("modified_within_hours"), int):
            hours = output.get("modified_within_hours")

        scanned = scan_files_on_filesystem(raw_path, pattern, modified_within_hours=hours)
        if scanned.get("error"):
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="search_files_filesystem",
                details={"reason": "folder_missing", "error": scanned.get("error")},
                observation=Observation(source="filesystem", data=scanned),
            )

        matches = filter_search_matches_for_pattern(list(scanned.get("matches") or []), pattern)
        verified_output = {
            "folder": scanned.get("folder"),
            "pattern": pattern,
            "count": len(matches),
            "matches": matches,
            "verified": True,
        }
        observation = Observation(
            source="filesystem",
            data={"verified_output": verified_output, "scan": scanned},
        )
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            method="search_files_filesystem",
            details={
                "result_count": len(matches),
                "source_location": verified_output.get("folder"),
                "file_pattern": pattern,
            },
            observation=observation,
        )


class CopyFileVerifier(BaseVerifier):
    tool_names = ("copy_file",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="copy_file_filesystem",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )

        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        source_raw = str(
            output.get("source") or ctx.tool_arguments.get("source") or ""
        ).strip()
        dest_raw = str(
            output.get("destination") or ctx.tool_arguments.get("destination") or ""
        ).strip()
        if not dest_raw and not source_raw:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="copy_file_filesystem",
                details={"reason": "paths_missing"},
            )

        from hermes.mission.step_context import expected_copy_destination

        if dest_raw and source_raw:
            dest_path = expected_copy_destination(source_raw, dest_raw)
        else:
            dest_path = Path(dest_raw)

        src_path = Path(source_raw) if source_raw else None
        observation = Observation(
            source="filesystem",
            data={
                "source": source_raw or None,
                "destination": str(dest_path),
                "exists": dest_path.is_file(),
                "size": dest_path.stat().st_size if dest_path.is_file() else None,
            },
        )

        if not dest_path.is_file():
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="copy_file_filesystem",
                details={"reason": "destination_missing", "destination": str(dest_path)},
                observation=observation,
            )

        if src_path is not None and src_path.is_file():
            try:
                if dest_path.stat().st_size != src_path.stat().st_size:
                    return VerificationResult(
                        status=VerificationStatus.FAILED,
                        method="copy_file_filesystem",
                        details={
                            "reason": "size_mismatch",
                            "source": str(src_path),
                            "destination": str(dest_path),
                        },
                        observation=observation,
                    )
            except OSError as exc:
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    method="copy_file_filesystem",
                    details={"reason": "stat_failed", "error": str(exc)},
                    observation=observation,
                )

        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            method="copy_file_filesystem",
            details={"destination": str(dest_path), "size": dest_path.stat().st_size},
            observation=observation,
        )


async def _observe_screen_state(ctx: VerifierContext) -> tuple[VerificationResult | None, dict[str, Any]]:
    if ctx.observe_tool is None:
        return None, {}
    observed = await ctx.observe_tool("read_screen_text", {}, ctx.run_id)
    if observed is None or not observed.success or not isinstance(observed.output, dict):
        return (
            VerificationResult(
                status=VerificationStatus.FAILED,
                method="screen_post_action_observe",
                details={"reason": "observe_failed", "error": getattr(observed, "error", None)},
            ),
            {},
        )
    from hermes.screen.observe import attach_screen_state

    payload = attach_screen_state(observed.output)
    observation = Observation(source="read_screen_text", data={"state_id": payload.get("state_id")})
    return None, {"payload": payload, "observation": observation}


class ResolveScreenEntityVerifier(BaseVerifier):
    tool_names = ("resolve_screen_entity",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        observation = Observation(source="execution_output", data=dict(output))
        if output.get("needs_user"):
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="screen_resolve",
                details={"reason": "ambiguous", "clarification": output.get("clarification")},
                observation=observation,
            )
        if not ctx.execution_success or not output.get("found"):
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="screen_resolve",
                details={"reason": "not_found", "error": ctx.execution_error},
                observation=observation,
            )
        if output.get("x") is None or output.get("y") is None:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="screen_resolve",
                details={"reason": "coordinates_missing"},
                observation=observation,
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            method="screen_resolve",
            details={"entity_id": output.get("entity_id"), "x": output.get("x"), "y": output.get("y")},
            observation=observation,
        )


class ClickScreenVerifier(BaseVerifier):
    tool_names = ("click",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="screen_click_observe",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        output = ctx.execution_output if isinstance(ctx.execution_output, dict) else {}
        prior_status = str(output.get("verification_status") or "").strip().casefold()
        if prior_status in {"unknown", "failed"}:
            status = (
                VerificationStatus.UNKNOWN
                if prior_status == "unknown"
                else VerificationStatus.FAILED
            )
            return VerificationResult(
                status=status,
                method="screen_click_observe",
                details={
                    "reason": "target_unproven" if prior_status == "unknown" else "click_failed",
                    "entity_id": output.get("entity_id"),
                },
            )
        pre_state = None
        try:
            from hermes.screen.store import last_screen_state

            pre_state = last_screen_state()
        except Exception:
            pre_state = None
        failed, data = await _observe_screen_state(ctx)
        if failed is not None:
            return failed
        if not data:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="screen_click_observe",
                details={"reason": "observe_unavailable"},
            )
        args = ctx.tool_arguments if isinstance(ctx.tool_arguments, dict) else {}
        payload = data["payload"]
        window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
        expected_text = str(args.get("text") or output.get("text") or "").strip()
        expected_id = str(args.get("entity_id") or output.get("entity_id") or "").strip()
        expected_ref = str(args.get("reference") or "").strip()
        post_text = str(payload.get("text") or "")
        post_title = str(
            payload.get("window_title")
            or payload.get("title")
            or window.get("title")
            or ""
        )
        post_url = str(payload.get("url") or window.get("url") or "")
        pre_window = pre_state.window if pre_state is not None else {}
        pre_state_id = str(
            args.get("state_id")
            or output.get("state_id")
            or (pre_state.state_id if pre_state is not None else "")
            or ""
        )
        pre_title = str((pre_window or {}).get("title") or "")
        pre_url = str((pre_window or {}).get("url") or "")
        pre_text = str(pre_state.text if pre_state is not None else "")
        ocr_changed = bool(pre_text) and pre_text.casefold() != post_text.casefold()
        destination_changed = bool(pre_title or pre_url) and (
            pre_title.casefold() != post_title.casefold()
            or pre_url.casefold() != post_url.casefold()
        )
        evidence = {
            "click_x": args.get("x", output.get("x")),
            "click_y": args.get("y", output.get("y")),
            "entity_id": expected_id,
            "expected_text": expected_text,
            "bbox": args.get("bbox") or output.get("bbox"),
            "pre_state_id": pre_state_id,
            "post_state_id": payload.get("state_id"),
            "pre_page_title": pre_title,
            "page_title": post_title,
            "pre_url": pre_url,
            "url": post_url,
            "ocr_changed": ocr_changed,
            "destination_changed": destination_changed,
            "reference": expected_ref,
            "post_click_text": post_text[:500],
        }
        target = expected_text.casefold()
        title_url = f"{post_title} {post_url}".casefold()
        if target and target in title_url:
            status = VerificationStatus.VERIFIED
            reason = "target_in_title_or_url"
        elif target and destination_changed and (post_title or post_url) and target not in title_url:
            status = VerificationStatus.FAILED
            reason = "wrong_target_opened"
        else:
            status = VerificationStatus.UNKNOWN
            reason = "target_unproven"
        _logger.info(
            "screen_click_verify",
            status=status.value,
            reason=reason,
            **{key: value for key, value in evidence.items() if key != "post_click_text"},
        )
        return VerificationResult(
            status=status,
            method="screen_click_observe",
            details={"reason": reason, **evidence, "verified_output": payload},
            observation=data["observation"],
        )


class ScrollScreenVerifier(BaseVerifier):
    tool_names = ("scroll",)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="screen_scroll_observe",
                details={"reason": "execution_failed", "error": ctx.execution_error},
            )
        failed, data = await _observe_screen_state(ctx)
        if failed is not None:
            return failed
        if not data:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="screen_scroll_observe",
                details={"reason": "observe_unavailable"},
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            method="screen_scroll_observe",
            details={"state_id": data["payload"].get("state_id"), "verified_output": data["payload"]},
            observation=data["observation"],
        )
