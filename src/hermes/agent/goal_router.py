from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermes.agent.goal_parser import ParsedGoal, parse_goal
from hermes.agent.local_intent import LocalIntent
from hermes.agent.scope_resolver import ExecutionScope, resolve_execution_scope
from hermes.agent.tool_intent import ToolIntentMatcher, ToolIntentResult
from hermes.context.conversational_context import ConversationalContext
from hermes.tools.catalog import ToolCatalog, build_tool_catalog
from hermes.tools.registry import ToolRegistry
from hermes.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class GoalRoute:
    intent: LocalIntent | None = None
    ambiguous: bool = False
    clarification: str = ""
    resolved_references: dict[str, str] = field(default_factory=dict)
    scope: ExecutionScope = ExecutionScope.CLIENT
    confidence: float = 0.0
    source: str = ""
    parsed_goal: ParsedGoal | None = None


class GoalRouter:
    """
    Goal understanding layer between reference resolution and legacy heuristics.

    Uses conversational context + tool catalog matching. Does not replace
    ReferenceResolver for deictic file/folder/content operations.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._matcher = ToolIntentMatcher(registry)
        self._catalog = build_tool_catalog(registry)

    @property
    def catalog(self) -> ToolCatalog:
        return self._catalog

    def route(
        self,
        message: str,
        ctx: ConversationalContext,
        *,
        resolved_references: dict[str, str] | None = None,
    ) -> GoalRoute:
        from hermes.agent.task_planner import has_actionable_sequence, is_multi_step_message

        parsed = parse_goal(message, ctx, resolved_references=resolved_references)
        logger.info("GOAL_PARSED", **parsed.to_dict())

        scope = resolve_execution_scope(message)
        if scope is ExecutionScope.SERVER:
            return GoalRoute(scope=scope, source="server_scope", parsed_goal=parsed)

        if parsed.ambiguity == "referans_netlestirme" and not resolved_references:
            return GoalRoute(
                ambiguous=True,
                clarification="Hangi dosya veya klasoru kastettigini netlestirir misin?",
                scope=scope,
                source="goal_parser",
                parsed_goal=parsed,
            )

        if is_multi_step_message(message) and has_actionable_sequence(message):
            return GoalRoute(scope=scope, source="multi_step", parsed_goal=parsed)

        if parsed.is_multi_step and parsed.confidence >= 0.75 and not parsed.requires_ui:
            return GoalRoute(scope=scope, source="multi_step_goal", parsed_goal=parsed)

        match: ToolIntentResult = self._matcher.match(
            message,
            ctx,
            resolved_references=resolved_references,
        )
        if match.ambiguous:
            return GoalRoute(
                ambiguous=True,
                clarification=match.clarification,
                resolved_references=match.resolved_references,
                scope=scope,
                source="tool_intent",
                parsed_goal=parsed,
            )
        if match.intent and match.confidence >= 0.8:
            tool_name = match.intent.request.name
            if tool_name in self._catalog.background_tools() and not parsed.requires_ui:
                logger.info(
                    "BACKGROUND_TOOL_SELECTED",
                    tool=tool_name,
                    opens_ui=False,
                )
            return GoalRoute(
                intent=match.intent,
                resolved_references=match.resolved_references,
                scope=scope,
                confidence=match.confidence,
                source="tool_intent",
                parsed_goal=parsed,
            )
        return GoalRoute(scope=scope, source="unmatched", parsed_goal=parsed)
