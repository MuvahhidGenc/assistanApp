from __future__ import annotations

import socket
from typing import Any

from hermes.client.session_store import get_client_id
from hermes.tools.manifest import build_tool_manifest
from hermes.tools.registry import ToolRegistry


class LocalClientContext:
    """Builds session metadata and run instructions for Windows local tools."""

    def __init__(self, registry: ToolRegistry, client_name: str = "HERMES Windows Client") -> None:
        self._registry = registry
        self._client_name = client_name
        self._manifest = build_tool_manifest(registry)

    @property
    def manifest(self) -> list[dict[str, Any]]:
        return list(self._manifest)

    @property
    def tool_count(self) -> int:
        return len(self._manifest)

    def _hostname(self) -> str:
        try:
            return socket.gethostname()
        except OSError:
            return "windows"

    def build_session_metadata(self) -> dict[str, Any]:
        return {
            "client": self._client_name,
            "platform": "windows",
            "hostname": self._hostname(),
            "windows_client_id": get_client_id(),
            "local_tools_connected": True,
            "local_tools": [
                {"name": entry["name"], "description": entry["description"], "category": entry["category"]}
                for entry in self._manifest
            ],
            "local_tools_summary": (
                f"Windows client bagli — {self.tool_count} yerel tool. "
                "Word/docx, silme, indirme, git clone, DNS, ekran islemleri desteklenir."
            ),
            "manifest_version": self.tool_count,
            "manifest_tools": sorted(entry["name"] for entry in self._manifest),
            "capabilities": {
                "computer_control": True,
                "system_info": True,
                "network_read": True,
                "interactive_input": True,
                "download_files": True,
                "git_clone": True,
                "package_install": True,
                "multi_step_agent": True,
                "local_rpc": {
                    "url": "http://127.0.0.1:8765",
                    "tool_endpoint": "POST /v1/tool/{name}",
                    "auth_header": "X-Secret",
                    "secret_file": "%LOCALAPPDATA%\\HermesClient\\state\\client.json",
                    "secret_key": "rpc_secret",
                },
            },
        }

    def build_run_instructions(self) -> str:
        names = ", ".join(sorted(entry["name"] for entry in self._manifest))
        return (
            "You are Hermes AI controlling the user's Windows PC through the HERMES client.\n"
            "The client ALWAYS has local tools — never say they are missing or unavailable.\n"
            "Never refuse PC actions. Never only open a browser when the user asked to download, install, or clone.\n"
            "NEVER use browser-use or server-side Chrome/CDP — it cannot control the user's PC browser.\n"
            "For YouTube, websites, or Chrome: always LOCAL_TOOL open_url (autoplay=true for YouTube search/video).\n"
            "For scroll / click visible text / back / fullscreen / show desktop:\n"
            "  scroll {direction, amount}, click_text {text}, browser_nav {action}, show_desktop, read_screen_text\n"
            "Voice follow-ups like 'scroll down', 'click VIDEO TITLE', 'go back', 'show desktop' must use these tools.\n\n"
            "EXECUTION PROTOCOL:\n"
            "1. Understand the full goal (download app, clone repo, install, configure, open, verify).\n"
            "2. Break it into ordered steps YOU execute via LOCAL_TOOL one at a time.\n"
            "3. Reply with ONLY one line per turn:\n"
            '   LOCAL_TOOL {"name": "<tool>", "arguments": {...}}\n'
            "4. Wait for TOOL_RESULT from the client, then continue with the next step.\n"
            "5. After all steps succeed, give a short Turkish summary in chat.\n\n"
            f"Available tools: {names}\n\n"
            "TOOL SELECTION (critical):\n"
            "- User wants file/app downloaded → download_file (NOT open_url)\n"
            "- GitHub/GitLab repo clone → git_clone with repo_url and target_dir\n"
            "- Install software → install_program (winget) — NEVER run_command for install\n"
            "- LibreOffice → install_program package=libreoffice (id: TheDocumentFoundation.LibreOffice)\n"
            "- Open website/video only when user explicitly wants to browse/watch → open_url\n"
            "- Create folder → create_folder, then download/clone into it\n"
            "- Word (.docx) with text → create_word_document (NOT run_command, NOT open_url)\n"
            "- Text file → write_file\n"
            "- Delete file/folder → delete_path (recursive=true for folders with content)\n"
            "- Verify result → list_directory on parent folder; use verified_listing in TOOL_RESULT\n"
            "- Custom shell steps → run_command (powershell), requires user approval\n"
            "- DNS/network → set_dns, get_network_config\n"
            "- Screen → read_screen_text, screenshot\n\n"
            "EXAMPLE — 'Chrome indir ve kur':\n"
            "  Step 1: download_file url=<direct_or_page_url> path=Downloads\\chrome_setup.exe\n"
            "  Step 2: run_command command=Start-Process ... -Wait\n"
            "  OR: install_program package=Google.Chrome\n\n"
            "EXAMPLE — 'GitHub repomu cek github.com/user/repo':\n"
            "  Step 1: create_folder path=Projects\\repo-name (optional)\n"
            "  Step 2: git_clone repo_url=https://github.com/user/repo.git target_dir=...\n\n"
            "RULES:\n"
            "- Never claim a file was created/deleted without list_directory or verified_listing proof.\n"
            "- Never stop after open_url if the task is download/install/clone.\n"
            "- Use SON_YEREL_ISLEMLER context to continue multi-step work.\n"
            "- Ask in chat only when repo URL, file path, or app name is truly ambiguous.\n"
            "- Keep voice/chat summary short; put paths, logs, errors in chat text.\n"
        )

    def capabilities_notice(self) -> str:
        names = ", ".join(sorted(entry["name"] for entry in self._manifest)[:10])
        suffix = "..." if self.tool_count > 10 else ""
        return f"Bu Windows client su local tools'a sahip ({self.tool_count}): {names}{suffix}"
