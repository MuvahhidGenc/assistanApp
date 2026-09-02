"""Bulk and high-impact operation risk checks."""
from __future__ import annotations

import re
from dataclasses import dataclass

_BULK_COUNT = re.compile(
    r"\b(\d{2,})\s+(?:dosya(?:yi|yı)?|file|oge(?:yi|yi)?|öge(?:yi|yi)?|item)\b",
    re.IGNORECASE,
)
_BULK_ALL = re.compile(
    r"\b(tum(?:unu|u|ünü)?|hepsini|all\s+files|klas(?:o|ö)r(?:un)?\s+tamamini)\b",
    re.IGNORECASE,
)


@dataclass
class BulkRiskAssessment:
    requires_confirmation: bool = False
    reason: str = ""
    estimated_count: int | None = None


def assess_bulk_risk(tool_name: str, message: str, arguments: dict | None = None) -> BulkRiskAssessment:
    name = (tool_name or "").casefold()
    text = (message or "").casefold()
    args = arguments or {}

    if name != "delete_path":
        return BulkRiskAssessment()

    count_match = _BULK_COUNT.search(text)
    if count_match:
        count = int(count_match.group(1))
        if count >= 10:
            return BulkRiskAssessment(
                requires_confirmation=True,
                reason=f"{count} dosya silinecek. Devam edeyim mi?",
                estimated_count=count,
            )

    if _BULK_ALL.search(text):
        return BulkRiskAssessment(
            requires_confirmation=True,
            reason="Toplu silme istegi algilandi. Devam edeyim mi?",
        )

    if args.get("recursive") and args.get("path"):
        path = str(args["path"]).casefold()
        if any(token in path for token in ("downloads", "indirilen", "desktop", "masaust")):
            return BulkRiskAssessment(
                requires_confirmation=True,
                reason="Buyuk klasor silme istegi. Devam edeyim mi?",
            )

    return BulkRiskAssessment()
