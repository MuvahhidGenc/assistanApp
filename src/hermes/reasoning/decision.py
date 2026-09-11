"""Decision data model — typed contracts for what the runtime can decide.

The reasoning layer picks exactly one of:

  * `Action`               — call a capability with arguments.
  * `ObservationRequest`   — request evidence from the client (no side effect).
  * `UserQuestion`         — ask the user for clarification.
  * `Complete`             — the objective is achieved with evidence.
  * `ReReason`             — the current evidence is insufficient; call the LLM again.

These are the *only* outcomes. There is no "fallback", no "legacy path",
no "default decision". If the reasoning layer cannot produce one of these,
the orchestrator surfaces that fact to the user — never a silent shim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DecisionKind(StrEnum):
    ACTION = "action"
    OBSERVATION_REQUEST = "observation_request"
    USER_QUESTION = "user_question"
    COMPLETE = "complete"
    RE_REASON = "re_reason"


@dataclass(frozen=True)
class Action:
    """A capability call the runtime wants the orchestrator to attempt."""

    capability: str
    arguments: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    expected_observation: str = ""


@dataclass(frozen=True)
class ObservationRequest:
    """The runtime wants read-only evidence before deciding further."""

    observation_type: str             # "screen.snapshot" | "filesystem.list" | …
    target: str = ""                  # what to observe (path, url, …)
    rationale: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UserQuestion:
    """A question to put to the user before proceeding."""

    question: str
    why: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Complete:
    """The runtime believes the objective is met, with evidence.

    `evidence_ids` are references to evidence records in the World Model.
    The orchestrator is responsible for confirming each evidence claim;
    the runtime never asserts goal completion on faith.
    """

    summary: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReReason:
    """The runtime asks itself to think again.

    The orchestrator should call `runtime.reason(...)` once more with the
    same context (which has now gained evidence from the action that just
    ran) before any new action.
    """

    reason: str = ""


@dataclass(frozen=True)
class MemoryFact:
    """A durable fact the user explicitly asked Hermes to remember."""

    key: str
    value: str


@dataclass(frozen=True)
class Decision:
    """A typed decision produced by the reasoning layer."""

    kind: DecisionKind
    action: Action | None = None
    observation_request: ObservationRequest | None = None
    user_question: UserQuestion | None = None
    complete: Complete | None = None
    re_reason: ReReason | None = None
    correlation_id: str = ""
    required_capabilities: tuple[str, ...] = ()
    memory_facts: tuple[MemoryFact, ...] = ()

    # ---- factories ---------------------------------------------------------

    @staticmethod
    def of_action(
        capability: str,
        arguments: dict[str, Any] | None = None,
        *,
        rationale: str = "",
        expected_observation: str = "",
        correlation_id: str = "",
    ) -> "Decision":
        return Decision(
            kind=DecisionKind.ACTION,
            action=Action(
                capability=capability,
                arguments=dict(arguments or {}),
                rationale=rationale,
                expected_observation=expected_observation,
            ),
            correlation_id=correlation_id,
        )

    @staticmethod
    def of_observation_request(
        observation_type: str,
        target: str = "",
        *,
        rationale: str = "",
        parameters: dict[str, Any] | None = None,
        correlation_id: str = "",
    ) -> "Decision":
        return Decision(
            kind=DecisionKind.OBSERVATION_REQUEST,
            observation_request=ObservationRequest(
                observation_type=observation_type,
                target=target,
                rationale=rationale,
                parameters=dict(parameters or {}),
            ),
            correlation_id=correlation_id,
        )

    @staticmethod
    def of_user_question(
        question: str,
        *,
        why: str = "",
        options: tuple[str, ...] = (),
        correlation_id: str = "",
    ) -> "Decision":
        return Decision(
            kind=DecisionKind.USER_QUESTION,
            user_question=UserQuestion(question=question, why=why, options=options),
            correlation_id=correlation_id,
        )

    @staticmethod
    def of_complete(
        summary: str,
        evidence_ids: tuple[str, ...] = (),
        *,
        correlation_id: str = "",
    ) -> "Decision":
        return Decision(
            kind=DecisionKind.COMPLETE,
            complete=Complete(summary=summary, evidence_ids=evidence_ids),
            correlation_id=correlation_id,
        )

    @staticmethod
    def of_re_reason(reason: str = "", *, correlation_id: str = "") -> "Decision":
        return Decision(
            kind=DecisionKind.RE_REASON,
            re_reason=ReReason(reason=reason),
            correlation_id=correlation_id,
        )

    # ---- invariants --------------------------------------------------------

    def __post_init__(self) -> None:
        """Enforce the one-payload-per-decision invariant.

        A decision of kind ACTION must carry only an `Action` payload. The
        orchestrator and tests rely on this — a sloppy Decision factory
        must raise, not silently carry competing payloads.
        """
        pairs = {
            DecisionKind.ACTION: self.action,
            DecisionKind.OBSERVATION_REQUEST: self.observation_request,
            DecisionKind.USER_QUESTION: self.user_question,
            DecisionKind.COMPLETE: self.complete,
            DecisionKind.RE_REASON: self.re_reason,
        }
        expected = pairs[self.kind]
        if expected is None:
            raise ValueError(
                f"Decision of kind {self.kind.value!r} is missing its payload."
            )
        for other_kind, other_payload in pairs.items():
            if other_kind is self.kind:
                continue
            if other_payload is not None:
                raise ValueError(
                    f"Decision of kind {self.kind.value!r} must not also carry "
                    f"a {other_kind.value!r} payload."
                )