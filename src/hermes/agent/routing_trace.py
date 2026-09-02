from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermes.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RoutingTrace:
    user_goal: str = ""
    resolved_target: str = ""
    selected_tool: str = ""
    execution_target: str = "client"
    arguments: dict[str, Any] = field(default_factory=dict)
    route_source: str = ""
    executed: bool = False
    verified: bool = False
    final_result: str = ""

    def log(self) -> None:
        logger.info(
            "routing_trace",
            USER_GOAL=self.user_goal,
            RESOLVED_TARGET=self.resolved_target,
            SELECTED_TOOL=self.selected_tool,
            EXECUTION_TARGET=self.execution_target,
            ARGUMENTS=self.arguments,
            ROUTE_SOURCE=self.route_source,
            EXECUTED=self.executed,
            VERIFIED=self.verified,
            FINAL_RESULT=self.final_result,
        )
