from __future__ import annotations

import re

from hermes.agent.server_tasks import should_defer_to_server
from hermes.agent.task_planner import has_actionable_sequence, is_multi_step_message, plan_local_sequence
from hermes.mission.compound_goal import is_compound_chained_file_mission
from hermes.mission.write_content import is_literal_composite_file_mission, requires_tool_output_dependency

_MISSION_SIGNALS = (
    "standart pc",
    "pc kurulum",
    "bilgisayar kurulum",
    "pc kurulumu",
    "analiz et",
    "analiz et,",
    "repoyu analiz",
    "github reposunu",
    "repo analiz",
    "kur ve calistir",
    "kur ve çalıştır",
    "optimize et",
    "optimiz",
    "guvenli sekilde",
    "güvenli şekilde",
    "favoriye ekle",
    "masaustune uygulama",
    "masaüstüne uygulama",
    "pwa",
    "adim adim",
    "adım adım",
    "pdf",
    "kopyala",
    "copy",
    "tasi",
    "taşı",
    "move",
    "bul ve",
    "ara ve",
    "arastir",
    "araştır",
    "incele ve kur",
    "developer bilgisayar",
    "it bilgisayar",
)

_MISSION_PATTERNS = (
    re.compile(r"analiz\s+et", re.IGNORECASE),
    re.compile(r"incele\s+ve", re.IGNORECASE),
    re.compile(r"optimize\s+et", re.IGNORECASE),
    re.compile(r"kur\s+ve\s+calistir", re.IGNORECASE),
    re.compile(r"kur\s+ve\s+çalıştır", re.IGNORECASE),
    re.compile(r"github\.com/\S+", re.IGNORECASE),
)


def is_fast_path_candidate(message: str) -> bool:
    """
    True when the existing orchestrator fast paths should handle the message
    without entering Mission Engine tracking.
    """
    text = (message or "").strip()
    if not text:
        return False

    from hermes.agent.local_intent import guess_file_action, guess_install_action, guess_local_action
    from hermes.mission.write_content import (
        is_composite_file_mission,
        is_literal_composite_file_mission,
        requires_tool_output_dependency,
    )

    if is_compound_chained_file_mission(text):
        return False

    if requires_tool_output_dependency(text):
        return False

    if is_literal_composite_file_mission(text):
        return True

    if is_composite_file_mission(text):
        return False

    from hermes.agent.goal_router import GoalRouter
    from hermes.context.conversational_context import ConversationalContext
    from hermes.tools.registry import create_default_registry

    if guess_file_action(text) or guess_install_action(text):
        return True

    route = GoalRouter(create_default_registry()).route(text, ConversationalContext())
    if route.intent and route.confidence >= 0.8:
        return True

    if not should_defer_to_server(text):
        if requires_tool_output_dependency(text):
            return False
        if has_actionable_sequence(text):
            steps = plan_local_sequence(text)
            if len(steps) >= 2:
                return True
        if guess_local_action(text):
            return True

    return False


def should_create_mission(message: str) -> bool:
    """
    Independent of should_defer_to_server().

    Complex, goal-oriented, multi-step or research-heavy tasks should enter
    Mission Engine even when server deferral heuristics do not fire.
    """
    text = (message or "").strip()
    if not text:
        return False

    lower = text.casefold()

    if is_compound_chained_file_mission(text):
        return True

    from hermes.agent.plan_analysis import is_create_and_audit_goal, is_organize_files_goal
    from hermes.agent.general_goal import is_general_mission_goal

    if is_organize_files_goal(text) or is_create_and_audit_goal(text) or is_general_mission_goal(text):
        return True

    if requires_tool_output_dependency(text):
        return True

    if any(signal in lower for signal in _MISSION_SIGNALS):
        return True

    if any(pattern.search(text) for pattern in _MISSION_PATTERNS):
        return True

    if is_multi_step_message(text) and len(text) > 60:
        return True

    if "pdf" in lower and any(token in lower for token in ("kopyala", "copy", "tasi", "taşı", "move")):
        return True

    if lower.count(" ve ") >= 2 and len(text) > 40:
        return True

    if " sonra " in lower and len(text) > 35:
        compound_signals = ("favori", "masaust", "masaüst", "uygulama", "kur", "analiz", "optimize")
        if any(signal in lower for signal in compound_signals):
            return True

    return False


def should_route_to_mission(message: str) -> bool:
    """Fast path wins over mission routing, except dynamic tool-output file missions."""
    text = (message or "").strip()
    lower = text.casefold()
    if is_compound_chained_file_mission(text):
        return True
    if is_literal_composite_file_mission(text):
        return False
    if requires_tool_output_dependency(text):
        return True
    if "pdf" in lower and any(
        token in lower
        for token in ("kopyala", "copy", "tasi", "taşı", "move", "oku", "read", "ozet", "özet")
    ):
        return True
    if is_fast_path_candidate(message):
        return False
    return should_create_mission(message)
