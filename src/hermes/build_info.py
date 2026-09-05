"""Build identity logged at runtime so EXE version can be verified."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

BUILD_LABEL = "screen-video-verify-v1"


def _git_revision() -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


GIT_REVISION = _git_revision()
HERMES_BUILD_VERSION = f"{BUILD_LABEL}-{GIT_REVISION}"
BUILD_TIMESTAMP = datetime.now(timezone.utc).isoformat()
