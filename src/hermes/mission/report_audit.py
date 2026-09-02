"""Audit trail for browser report write / verify / read resolution."""
from __future__ import annotations

from typing import Any

from hermes.mission.goal_verification import append_goal_audit


def log_report_chain(mission: Any, stage: str, **fields: Any) -> None:
    append_goal_audit(mission, f"REPORT_{stage.upper()}", **fields)
