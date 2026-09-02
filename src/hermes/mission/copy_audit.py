"""Structured audit trail for PDF copy_search_matches runtime tracing."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes.utils.logging import get_logger

logger = get_logger(__name__)


def _ext(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).suffix.casefold() or None


def audit_copy_chain(
    stage: str,
    *,
    mission_id: str | None = None,
    step_id: str | None = None,
    depends_on: list[str] | None = None,
    source: str | None = None,
    destination: str | None = None,
    extension: str | None = None,
    verified: bool | None = None,
    matched_files: list[str] | None = None,
    result_count: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "stage": stage,
        "mission_id": mission_id,
        "step_id": step_id,
        "depends_on": depends_on,
        "source": source,
        "destination": destination,
        "extension": extension or _ext(source or destination),
        "verified": verified,
        "result_count": result_count,
    }
    if matched_files is not None:
        payload["matched_files_count"] = len(matched_files)
        payload["matched_files_sample"] = matched_files[:5]
        payload["matched_extensions"] = sorted(
            {_ext(item) for item in matched_files if _ext(item)}
        )
    if extra:
        payload.update(extra)
    logger.info("COPY_CHAIN_AUDIT", **{k: v for k, v in payload.items() if v is not None})
