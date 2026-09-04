from __future__ import annotations

import json
import platform
from typing import Any

from hermes.mission.schema import PlanningContext
from hermes.mission.write_content import (
    build_write_content_planning_hints,
    requires_tool_output_dependency,
)
from hermes.skills.loader import match_skills_for_goal
from hermes.tools.manifest import input_schema_for_tool
from hermes.tools.registry import ToolRegistry

_READ_ONLY_VERIFY_TOOLS = frozenset(
    {
        "get_system_info",
        "list_installed_programs",
        "get_network_config",
        "list_processes",
        "list_services",
        "screenshot",
        "read_screen_text",
        "ping_host",
        "dns_lookup",
        "list_directory",
    }
)


def build_system_state() -> dict[str, Any]:
    return {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "node": platform.node(),
    }


def _truncate(text: str, limit: int = 100) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def build_compact_tool_manifest(registry: ToolRegistry) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for definition in sorted(registry.list_tools(), key=lambda d: (d.category, d.name)):
        tool = registry.get(definition.name)
        schema = input_schema_for_tool(definition.name, tool)
        properties = schema.get("properties") or {}
        entries.append(
            {
                "name": definition.name,
                "description": _truncate(definition.description, 100),
                "category": definition.category,
                "risk_level": definition.risk_level.value,
                "input_schema": {
                    "properties": list(properties.keys()),
                    "required": list(schema.get("required") or []),
                },
                "verification_capable": definition.name in _READ_ONLY_VERIFY_TOOLS,
            }
        )
    return entries


def build_planning_context(
    user_goal: str,
    registry: ToolRegistry,
    *,
    relevant_context: dict[str, Any] | None = None,
) -> PlanningContext:
    skills = [skill.to_dict() for skill in match_skills_for_goal(user_goal)]
    extra = dict(relevant_context or {})
    write_hints = build_write_content_planning_hints(user_goal)
    if write_hints:
        extra["write_file_argument_hints"] = write_hints
    if requires_tool_output_dependency(user_goal):
        extra["dynamic_tool_output_hints"] = {
            "requires_prior_tool_output": True,
            "rules": [
                "Never call write_file with placeholder/default content when user asked for dynamic data",
                "Collect data with tools like get_system_info before write_file",
                "Use a logical step to prepare file content from prior tool output",
                "write_file.content must come from get_system_info output, not static text",
            ],
            "required_tools_before_write_file": ["get_system_info"],
        }
    return PlanningContext(
        user_goal=user_goal.strip(),
        system_state=build_system_state(),
        tools=build_compact_tool_manifest(registry),
        skills=skills,
        relevant_context=extra,
        capabilities=list(registry.capabilities()),
    )


def build_agent_context_for_planning(
    working_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge Phase 8 verified state into mission planning context."""
    ctx_data = dict(working_context or {})
    agent_state = dict(ctx_data.get("agent_state") or {})
    goal_analysis = dict(ctx_data.get("goal_analysis") or {})
    merged = {
        "resolved_references": ctx_data.get("resolved_references") or {},
        "agent_state": agent_state,
        "goal_analysis": goal_analysis,
        "verified_file": agent_state.get("verified_file"),
        "verified_folder": agent_state.get("verified_folder"),
        "recent_files": agent_state.get("recent_files") or [],
        "recent_folders": agent_state.get("recent_folders") or [],
    }
    if goal_analysis.get("desired_state"):
        merged["desired_state"] = goal_analysis["desired_state"]
    if goal_analysis.get("current_state_summary"):
        merged["current_state"] = goal_analysis["current_state_summary"]
    generic_goal = ctx_data.get("generic_goal")
    if isinstance(generic_goal, dict):
        merged["generic_goal"] = generic_goal
    return merged


def build_planning_prompt(context: PlanningContext) -> str:
    payload = context.to_payload()
    return (
        "Sen HERMES Windows istemcisinin niyet planlayicisisin. "
        "Kullanicinin amacini yetenek (capability) cinsinden analiz et. "
        "Asla arac adi veya executable cagri yazma. "
        "risk_hint yalnizca tahmindir; gercek riski sen belirleyemezsin.\n\n"
        "PLANNING CONTEXT:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Kurallar:\n"
        "- Yalnizca available_capabilities icindeki yetenek adlarini kullan.\n"
        "- Arac adi yazma; tool secimini yerel cozumleyici yapar.\n"
        "- relevant_context.write_file_argument_hints.required_content varsa "
        "plan adiminin inputs.content alani birebir o deger olmali.\n"
        "- Kullanicinin meta talimatlari (or. 'tam olarak', 'baska hicbir sey yazma') "
        "content'e ASLA yazilmaz.\n"
        "- Gerekli degeri bilmiyorsan o adimi uydurma; needs_user_input=true yap.\n"
        "- mode: task | conversation | question.\n"
        "- relevant_context.resolved_references varsa hedefi tekrar tahmin etme.\n"
        "- relevant_context.agent_state.verified_file varsa son dosya referansinda onu kullan.\n\n"
        "Cikti formati (yalnizca JSON, baska metin yok):\n"
        '{"goal":"...","subgoals":[],"plan":[{"capability":"...","inputs":{}}],'
        '"required_capabilities":[],"constraints":[],"ambiguity":[],'
        '"needs_user_input":false,"clarifying_question":"","risk_hint":"read_only",'
        '"confidence":0.0,"expected_outcome":"...","mode":"task","reply":""}'
    )
