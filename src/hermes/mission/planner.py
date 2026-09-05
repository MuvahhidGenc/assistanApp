from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from hermes.mission.compound_goal import build_compound_desktop_file_plan
from hermes.mission.context import build_planning_context, build_planning_prompt
from hermes.mission.models import Mission, MissionStep, StepAction
from hermes.mission.step_context import (
    LOGICAL_KIND_FORMAT_SYSTEM_INFO,
    SOURCE_FIELD_PREPARED_CONTENT,
    SOURCE_FIELD_TOOL_OUTPUT_FORMATTED,
    extract_folder_and_file_paths,
    is_placeholder_content,
)
from hermes.mission.validator import validate_plan_steps
from hermes.mission.write_content import (
    normalize_plan_steps_write_content,
    planner_debug_enabled,
    requires_tool_output_dependency,
)
from hermes.server.client import HermesServerError
from hermes.server.models import ChatRequest
from hermes.skills.loader import match_skills_for_goal
from hermes.tools.registry import ToolRegistry
from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class PlannerClient(Protocol):
    async def chat(self, request: ChatRequest) -> dict[str, Any]: ...


class PlannerError(Exception):
    pass


class PlannerTimeoutError(PlannerError):
    pass


class PlannerFailureError(PlannerError):
    pass


@dataclass
class PlannerResult:
    success: bool
    steps: list[MissionStep]
    source: str = "ai"
    error: str | None = None
    raw_response: str | None = None
    argument_diagnostics: list[dict[str, Any]] = field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _extract_chat_content(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
    for key in ("output", "content", "text"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def build_dynamic_system_info_file_plan(user_goal: str, registry: ToolRegistry) -> list[MissionStep]:
    """Deterministic plan: folder → get_system_info → prepare content → write_file."""
    if not requires_tool_output_dependency(user_goal):
        return []
    if not all(name in registry for name in ("create_folder", "get_system_info", "write_file")):
        return []

    folder_path, file_path = extract_folder_and_file_paths(user_goal)
    return [
        MissionStep(
            step_id="create_target_folder",
            title="Hedef klasoru olustur",
            action=StepAction.TOOL,
            tool_name="create_folder",
            tool_arguments={"path": folder_path},
            depends_on=[],
            risk_level="normal_modification",
            expected_result=f"Klasor hazir: {folder_path}",
            verification={"required": True, "method": "output_present"},
        ),
        MissionStep(
            step_id="collect_system_info",
            title="Sistem bilgilerini topla",
            action=StepAction.TOOL,
            tool_name="get_system_info",
            tool_arguments={},
            depends_on=["create_target_folder"],
            risk_level="read_only",
            expected_result="Windows surumu, bilgisayar adi, CPU, RAM",
            verification={"required": True, "method": "system_info_fields"},
        ),
        MissionStep(
            step_id="prepare_system_file_content",
            title="Sistem bilgilerini dosya icerigi olarak hazirla",
            action=StepAction.LOGICAL,
            depends_on=["collect_system_info"],
            expected_result="Dosya icerigi hazir",
            verification={"required": True, "method": "prepared_system_info_content"},
            metadata={"logical_kind": LOGICAL_KIND_FORMAT_SYSTEM_INFO},
        ),
        MissionStep(
            step_id="write_system_file",
            title="Sistem bilgilerini dosyaya yaz",
            action=StepAction.TOOL,
            tool_name="write_file",
            tool_arguments={"path": file_path, "content": ""},
            depends_on=["prepare_system_file_content"],
            risk_level="normal_modification",
            expected_result=f"Dosya yazildi: {file_path}",
            verification={"required": True, "method": "output_present"},
            metadata={"content_from_step": "prepare_system_file_content"},
            argument_bindings=[
                {
                    "argument": "content",
                    "source_step_id": "prepare_system_file_content",
                    "source_field": SOURCE_FIELD_PREPARED_CONTENT,
                }
            ],
        ),
    ]


def _steps_by_id(steps: list[MissionStep]) -> dict[str, MissionStep]:
    return {step.step_id: step for step in steps}


def _transitive_depends_on(step_id: str, steps_by_id: dict[str, MissionStep]) -> set[str]:
    seen: set[str] = set()

    def visit(current_id: str) -> None:
        if current_id in seen:
            return
        seen.add(current_id)
        step = steps_by_id.get(current_id)
        if step is None:
            return
        for dep_id in step.depends_on:
            visit(dep_id)

    visit(step_id)
    return seen


def _find_prepare_logical_step(
    write_step: MissionStep,
    system_step: MissionStep,
    steps: list[MissionStep],
    steps_by_id: dict[str, MissionStep],
) -> MissionStep | None:
    write_deps = _transitive_depends_on(write_step.step_id, steps_by_id)
    logical_steps = [step for step in steps if step.action == StepAction.LOGICAL]
    candidates: list[MissionStep] = []
    for logical in logical_steps:
        logical_deps = _transitive_depends_on(logical.step_id, steps_by_id)
        if system_step.step_id not in logical_deps and system_step.step_id not in logical.depends_on:
            continue
        if logical.step_id in write_deps or logical.step_id in write_step.depends_on:
            candidates.append(logical)
    if not candidates:
        for dep_id in write_step.depends_on:
            dep = steps_by_id.get(dep_id)
            if dep is not None and dep.action == StepAction.LOGICAL:
                candidates.append(dep)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return candidates[-1]
    return None


def finalize_dynamic_file_plan(user_goal: str, steps: list[MissionStep]) -> list[MissionStep]:
    """
    Wire typed argument bindings and logical_kind for dynamic file missions.

    AI plans may include get_system_info but omit logical_kind / content bindings.
    """
    if not requires_tool_output_dependency(user_goal):
        return steps

    system_steps = [step for step in steps if step.tool_name == "get_system_info"]
    write_steps = [step for step in steps if step.tool_name == "write_file"]
    if not system_steps or not write_steps:
        return steps

    system_step = system_steps[0]
    steps_by_id = _steps_by_id(steps)

    for write_step in write_steps:
        content = str(write_step.tool_arguments.get("content") or "")
        if is_placeholder_content(content):
            write_step.tool_arguments["content"] = ""

        prepare_step = _find_prepare_logical_step(write_step, system_step, steps, steps_by_id)
        if prepare_step is not None:
            prepare_step.metadata["logical_kind"] = LOGICAL_KIND_FORMAT_SYSTEM_INFO
            if system_step.step_id not in prepare_step.depends_on:
                prepare_step.depends_on = list(
                    dict.fromkeys([*prepare_step.depends_on, system_step.step_id])
                )
            if prepare_step.step_id not in write_step.depends_on:
                write_step.depends_on = list(
                    dict.fromkeys([*write_step.depends_on, prepare_step.step_id])
                )
            write_step.metadata["content_from_step"] = prepare_step.step_id
            write_step.argument_bindings = [
                {
                    "argument": "content",
                    "source_step_id": prepare_step.step_id,
                    "source_field": SOURCE_FIELD_PREPARED_CONTENT,
                }
            ]
        else:
            if system_step.step_id not in write_step.depends_on:
                write_step.depends_on = list(
                    dict.fromkeys([*write_step.depends_on, system_step.step_id])
                )
            write_step.metadata["content_from_step"] = system_step.step_id
            write_step.argument_bindings = [
                {
                    "argument": "content",
                    "source_step_id": system_step.step_id,
                    "source_field": SOURCE_FIELD_TOOL_OUTPUT_FORMATTED,
                }
            ]

    return steps


def ensure_dynamic_file_plan(
    user_goal: str,
    steps: list[MissionStep],
    registry: ToolRegistry,
) -> list[MissionStep]:
    """Replace under-specified dynamic file plans that skip required tool outputs."""
    if not requires_tool_output_dependency(user_goal):
        return steps

    tool_names = {step.tool_name for step in steps if step.action == StepAction.TOOL}
    write_steps = [step for step in steps if step.tool_name == "write_file"]
    if write_steps and "get_system_info" not in tool_names:
        dynamic_plan = build_dynamic_system_info_file_plan(user_goal, registry)
        if dynamic_plan:
            return dynamic_plan
    return finalize_dynamic_file_plan(user_goal, steps)


def _goal_has_file_operations_intent(user_goal: str) -> bool:
    """Word-boundary intent detection — avoid matching 'ara' inside 'olarak'."""
    import re

    from hermes.agent.general_task_planner import is_pdf_inspect_goal

    text = (user_goal or "").casefold()
    if is_pdf_inspect_goal(user_goal):
        return False
    has_copy = bool(re.search(r"\b(kopyala|copy|tasi|taşı|move)\b", text))
    has_search = bool(re.search(r"\b(bul|ara|search)\b", text))
    known_source = "indirilenler" in text or "downloads" in text
    # Format mentions ("PDF olsun") are revisions, not copy/search missions.
    if has_copy and has_search:
        return True
    if has_search and known_source:
        return True
    if has_copy and known_source:
        return True
    return False


def build_file_operations_plan(user_goal: str, registry: ToolRegistry) -> list[MissionStep]:
    """Heuristic multi-step plan for search/copy/move file workflows."""
    if not _goal_has_file_operations_intent(user_goal):
        return []
    if not all(name in registry for name in ("search_files", "create_folder", "copy_file")):
        return []

    from pathlib import Path

    from hermes.context.system_paths import extract_file_type_pattern, resolve_known_folder

    goal = (user_goal or "").casefold()
    source = resolve_known_folder(user_goal) or resolve_known_folder("indirilenler")
    source_folder = str(source or (Path.home() / "Downloads"))
    dest_name = "Raporlar"
    import re

    folder_match = re.search(
        r"masa[uü]st(?:u|ü)(?:nde|de|ne)?\s+([\w\d_.-]+)\s+klas(?:o|ö)r(?:u|ü)?"
        r"(?:\s+(?:olustur|oluştur|yarat|create|olusturup|oluşturup))?",
        user_goal,
        re.IGNORECASE,
    )
    if folder_match:
        dest_name = folder_match.group(1).strip().title() or dest_name

    desktop = Path.home() / "Desktop"
    dest_folder = str(desktop / dest_name)
    pattern = extract_file_type_pattern(user_goal) or ("*.pdf" if "pdf" in goal else "*")
    hours = 48 if ("dun" in goal or "dün" in goal) else None

    steps = [
        MissionStep(
            step_id="search_source_files",
            title="Kaynak dosyalari bul",
            action=StepAction.TOOL,
            tool_name="search_files",
            tool_arguments={
                "path": source_folder,
                "pattern": pattern,
                **({"modified_within_hours": hours} if hours else {}),
            },
            depends_on=[],
            risk_level="read_only",
            expected_result="Eslesen dosyalar listelendi",
            verification={"required": True, "method": "search_files_filesystem"},
        ),
        MissionStep(
            step_id="ensure_destination_folder",
            title="Hedef klasoru hazirla",
            action=StepAction.TOOL,
            tool_name="create_folder",
            tool_arguments={"path": dest_folder},
            depends_on=["search_source_files"],
            risk_level="normal_modification",
            expected_result=f"Hedef klasor: {dest_folder}",
            verification={"required": True, "method": "output_present"},
        ),
        MissionStep(
            step_id="copy_matched_files",
            title="Dosyalari hedefe kopyala",
            action=StepAction.LOGICAL,
            depends_on=["search_source_files", "ensure_destination_folder"],
            expected_result="Eslesen dosyalar kopyalandi",
            verification={"required": True, "method": "copy_search_matches_filesystem"},
            metadata={"logical_kind": "copy_search_matches", "destination": dest_folder},
        ),
    ]
    return steps


def normalize_file_operations_plan(
    user_goal: str,
    steps: list[MissionStep],
    registry: ToolRegistry,
) -> list[MissionStep]:
    """Replace AI/cached copy_file plans with deterministic file_operations plan."""
    canonical = build_file_operations_plan(user_goal, registry)
    if not canonical:
        return steps
    tool_names = {step.tool_name for step in steps if step.action == StepAction.TOOL}
    has_copy_file = "copy_file" in tool_names
    has_logical_copy = any(
        step.metadata.get("logical_kind") == "copy_search_matches"
        for step in steps
        if step.action == StepAction.LOGICAL
    )
    if has_copy_file or not has_logical_copy:
        return canonical
    return steps


def build_heuristic_plan(user_goal: str, registry: ToolRegistry) -> list[MissionStep]:
    """Fallback plan when AI is unavailable. Uses skill hints, not fixed script order."""
    compound_plan = build_compound_desktop_file_plan(user_goal, registry)
    if compound_plan:
        return compound_plan

    file_plan = build_file_operations_plan(user_goal, registry)
    if file_plan:
        return file_plan

    dynamic_plan = build_dynamic_system_info_file_plan(user_goal, registry)
    if dynamic_plan:
        return dynamic_plan

    goal = (user_goal or "").casefold()
    skills = match_skills_for_goal(user_goal)
    preferred = skills[0].preferred_tools if skills else []

    steps: list[MissionStep] = [
        MissionStep(
            step_id="system_inspection",
            title="Sistem durumunu incele",
            action=StepAction.TOOL if "get_system_info" in registry else StepAction.LOGICAL,
            tool_name="get_system_info" if "get_system_info" in registry else None,
            tool_arguments={},
            depends_on=[],
            risk_level="read_only",
            expected_result="Temel sistem bilgisi",
            verification={"required": True, "method": "output_present"},
        ),
    ]

    if "list_installed_programs" in registry and (
        "kurulum" in goal or "pc" in goal or "standart" in goal or "list_installed_programs" in preferred
    ):
        steps.append(
            MissionStep(
                step_id="inspect_installed_software",
                title="Kurulu yazilimlari incele",
                action=StepAction.TOOL,
                tool_name="list_installed_programs",
                tool_arguments={"limit": 50},
                depends_on=["system_inspection"],
                risk_level="read_only",
                expected_result="Kurulu program listesi",
                verification={"required": True, "method": "output_present"},
            )
        )
        steps.append(
            MissionStep(
                step_id="determine_missing_software",
                title="Eksik yazilimlari belirle",
                action=StepAction.LOGICAL,
                depends_on=["inspect_installed_software"],
                expected_result="Kurulacak paket onerileri",
                verification={"required": False},
            )
        )

    if "get_network_config" in registry and ("ag" in goal or "network" in goal or "kurulum" in goal):
        steps.append(
            MissionStep(
                step_id="configure_network",
                title="Ag yapilandirmasini kontrol et",
                action=StepAction.TOOL,
                tool_name="get_network_config",
                tool_arguments={},
                depends_on=[steps[-1].step_id],
                risk_level="read_only",
                expected_result="Ag adapter bilgisi",
                verification={"required": True, "method": "output_present"},
            )
        )

    steps.append(
        MissionStep(
            step_id="verify_setup",
            title="Kurulumu dogrula",
            action=StepAction.LOGICAL,
            depends_on=[steps[-1].step_id],
            expected_result="Gorev tamamlandi ozeti",
            verification={"required": True, "method": "manual_review"},
        )
    )
    return steps


class MissionPlanner:
    """Single-shot mission planner. Does not execute tools."""

    def __init__(
        self,
        client: PlannerClient,
        registry: ToolRegistry,
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._client = client
        self._registry = registry
        self._timeout = timeout_seconds

    async def create_plan(self, mission: Mission, *, force_replan: bool = False) -> PlannerResult:
        if not force_replan and mission.plan_validated and mission.steps:
            normalized = normalize_file_operations_plan(
                mission.user_goal, list(mission.steps), self._registry
            )
            if [s.step_id for s in normalized] != [s.step_id for s in mission.steps]:
                validation = validate_plan_steps(
                    [step.to_dict() for step in normalized],
                    self._registry,
                )
                if validation.ok:
                    return PlannerResult(
                        success=True,
                        steps=validation.steps,
                        source="file_operations_heuristic",
                    )
            return PlannerResult(success=True, steps=list(mission.steps), source="cached")

        from hermes.agent.general_task_planner import build_pdf_inspect_plan, is_pdf_inspect_goal

        if is_pdf_inspect_goal(mission.user_goal):
            pdf_plan = build_pdf_inspect_plan(mission.user_goal, self._registry)
            if pdf_plan:
                validation = validate_plan_steps(
                    [step.to_dict() for step in pdf_plan],
                    self._registry,
                )
                if validation.ok:
                    return PlannerResult(
                        success=True,
                        steps=validation.steps,
                        source="pdf_inspect_heuristic",
                    )

        if mission.working_context.get("turn_kind") == "revise":
            file_plan = []
        else:
            file_plan = build_file_operations_plan(mission.user_goal, self._registry)
        if file_plan:
            validation = validate_plan_steps(
                [step.to_dict() for step in file_plan],
                self._registry,
            )
            if validation.ok:
                from hermes.mission.copy_audit import audit_copy_chain

                audit_copy_chain(
                    "planner_file_operations_plan",
                    mission_id=mission.mission_id,
                    result_count=len(validation.steps),
                    extra={
                        "step_ids": [step.step_id for step in validation.steps],
                        "source": "file_operations_heuristic",
                    },
                )
                return PlannerResult(
                    success=True,
                    steps=validation.steps,
                    source="file_operations_heuristic",
                )

        compound_plan = build_compound_desktop_file_plan(mission.user_goal, self._registry)
        if compound_plan:
            validation = validate_plan_steps(
                [step.to_dict() for step in compound_plan],
                self._registry,
            )
            if validation.ok:
                return PlannerResult(
                    success=True,
                    steps=validation.steps,
                    source="compound_file_heuristic",
                )

        from hermes.agent.plan_analysis import build_agent_dynamic_plan
        from hermes.mission.context import build_agent_context_for_planning
        from hermes.mission.write_content import requires_tool_output_dependency

        if not requires_tool_output_dependency(mission.user_goal):
            agent_ctx = build_agent_context_for_planning(mission.working_context)
            agent_plan = build_agent_dynamic_plan(
                mission.user_goal,
                self._registry,
                agent_context=agent_ctx,
            )
            if agent_plan:
                validation = validate_plan_steps(
                    [step.to_dict() for step in agent_plan],
                    self._registry,
                )
                if validation.ok:
                    return PlannerResult(
                        success=True,
                        steps=validation.steps,
                        source="agent_dynamic_heuristic",
                    )

        try:
            return await self._plan_with_ai(mission)
        except (PlannerTimeoutError, PlannerFailureError, HermesServerError) as exc:
            logger.warning("mission_planner_ai_failed", error=str(exc))
            steps = build_heuristic_plan(mission.user_goal, self._registry)
            validation = validate_plan_steps(
                [step.to_dict() for step in steps],
                self._registry,
            )
            if validation.ok:
                return PlannerResult(
                    success=True,
                    steps=validation.steps,
                    source="heuristic",
                    error=str(exc),
                )
            return PlannerResult(
                success=False,
                steps=[],
                source="heuristic",
                error=str(exc) + "; " + "; ".join(validation.errors[:3]),
            )

    async def _plan_with_ai(self, mission: Mission) -> PlannerResult:
        from hermes.intent.models import (
            AgentIntent,
            decide_confidence,
            llm_named_a_tool,
            repair_intent_json,
        )
        from hermes.intent.router import IntentRouter, RouteKind, build_input_pool
        from hermes.mission.context import build_agent_context_for_planning
        from hermes.tools.capabilities import capability_risk_floor

        relevant: dict[str, object] = build_agent_context_for_planning(mission.working_context)
        resolved = mission.working_context.get("resolved_references")
        if isinstance(resolved, dict) and resolved:
            relevant["resolved_references"] = resolved
        stored_intent = mission.working_context.get("agent_intent")
        if isinstance(stored_intent, dict) and stored_intent:
            relevant["previous_intent"] = {
                key: stored_intent.get(key)
                for key in ("goal", "required_capabilities", "expected_outcome")
            }
        context = build_planning_context(
            mission.user_goal,
            self._registry,
            relevant_context=relevant or None,
        )
        prompt = build_planning_prompt(context)
        request = ChatRequest(message=prompt, stream=False)

        try:
            response = await asyncio.wait_for(
                self._client.chat(request),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise PlannerTimeoutError("Planner timed out") from exc

        raw_text = _extract_chat_content(response)
        if not raw_text.strip():
            raise PlannerFailureError("Planner returned empty response")

        data = repair_intent_json(raw_text)
        if data is None:
            raise PlannerFailureError("Planner response did not contain valid intent JSON")
        if llm_named_a_tool(data):
            logger.warning(
                "mission_planner_ignored_tool_names",
                mission_id=mission.mission_id,
            )
            data = {
                key: value
                for key, value in data.items()
                if key not in ("tool_name", "tool", "steps")
            }
            if not data.get("required_capabilities") and not data.get("plan"):
                raise PlannerFailureError("planner named tools instead of capabilities")

        intent = AgentIntent.from_dict(data)
        router = IntentRouter(self._registry)
        floors = [
            floor
            for floor in (
                capability_risk_floor(self._registry, capability)
                for capability in intent.required_capabilities
            )
            if floor is not None
        ]
        risk_floor = max(floors, key=lambda level: level.severity) if floors else None
        confidence = decide_confidence(intent, risk_floor)
        plan = router.route(intent, confidence=confidence)

        if plan.kind is RouteKind.UNSUPPORTED:
            return PlannerResult(
                success=False,
                steps=[],
                source="capability_resolver",
                error=plan.reason or "gereken yetenek yok",
                raw_response=raw_text[:8000],
            )
        if plan.kind in (RouteKind.QUESTION, RouteKind.CONVERSATION):
            return PlannerResult(
                success=False,
                steps=[],
                source="capability_resolver",
                error=plan.question or plan.reason or "niyet yurutulemez",
                raw_response=raw_text[:8000],
            )

        steps = list(plan.steps)
        if plan.kind is RouteKind.SKILL and not steps:
            pool = build_input_pool(intent)
            steps, _unfillable = router._build_capability_steps(intent, pool)  # noqa: SLF001

        if not steps:
            raise PlannerFailureError("capability resolver produced no executable steps")

        validation = validate_plan_steps(
            [step.to_dict() for step in steps],
            self._registry,
        )
        if not validation.ok:
            raise PlannerFailureError("; ".join(validation.errors[:5]))

        steps, arg_diagnostics = normalize_plan_steps_write_content(
            mission.user_goal,
            validation.steps,
        )
        steps = ensure_dynamic_file_plan(mission.user_goal, steps, self._registry)
        steps = normalize_file_operations_plan(mission.user_goal, steps, self._registry)
        if arg_diagnostics:
            logger.info(
                "mission_planner_write_content_normalized",
                mission_id=mission.mission_id,
                diagnostics=arg_diagnostics,
            )
        if planner_debug_enabled():
            logger.info(
                "mission_planner_debug",
                mission_id=mission.mission_id,
                raw_plan_preview=raw_text[:4000],
                normalized_steps=[step.to_dict() for step in steps],
                argument_diagnostics=arg_diagnostics,
            )

        return PlannerResult(
            success=True,
            steps=steps,
            source="capability_resolver",
            raw_response=raw_text[:8000],
            argument_diagnostics=arg_diagnostics,
        )
