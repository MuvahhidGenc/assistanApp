"""World Model state — environment, task, evidence, reference.

The World Model holds four logically distinct kinds of state. They share
a single container so the runtime can snapshot them together and reason
over them coherently, but each kind has its own dataclass and its own
update API.

Conventions:

  * Every claim about the world has a *source* — see `EvidenceSource`.
  * The World Model never silently rewrites a fact; updates append evidence
    and bump a confidence, the way the Execution Log appends events.
  * The World Model never reads user language. It only ingests structured
    facts produced by tools, verifiers, observations, or by the runtime
    when the user has answered a question.

Why split environment / task / evidence / reference:

  * Environment state changes constantly and is read-only from the user's
    point of view. ReasoningRuntime asks "what's on screen right now?"
    not "what's the goal?".
  * Task state changes per turn. ReasoningRuntime owns it.
  * Evidence is the trail of *how we got here*. It must survive turns so
    later reasoning can re-examine an earlier claim.
  * References bind names ("the file we just opened") to concrete entities
    in environment state. They are the only way the runtime can answer
    "where is that thing?".
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.world_model.evidence import EvidenceRecord
    from hermes.world_model.reference import ReferenceBinding


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Environment state
# ---------------------------------------------------------------------------


@dataclass
class EnvironmentState:
    """What the client machine is, right now.

    Each field is optional; missing means "unknown" — the runtime must
    not invent values. The container carries an `as_of` timestamp so the
    reasoning layer knows how stale the snapshot is.
    """

    active_window: str | None = None
    open_applications: tuple[str, ...] = ()
    browser_url: str | None = None
    browser_title: str | None = None
    screen_state_id: str | None = None
    clipboard: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    as_of: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_window": self.active_window,
            "open_applications": list(self.open_applications),
            "browser_url": self.browser_url,
            "browser_title": self.browser_title,
            "screen_state_id": self.screen_state_id,
            "clipboard": self.clipboard,
            "extra": dict(self.extra),
            "as_of": self.as_of,
        }


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------


@dataclass
class TaskRequirement:
    """One thing the user asked for that the runtime must satisfy."""

    capability: str
    description: str = ""
    satisfied: bool = False
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "description": self.description,
            "satisfied": self.satisfied,
            "evidence": list(self.evidence),
        }


@dataclass
class TaskState:
    """The user's current objective, with the requirements it implies.

    `objective` is the user's stated goal in plain text. `requirements` is
    the runtime's decomposition into capabilities that must be satisfied.
    `uncertainty` lists open questions or partial verifications the runtime
    still needs to resolve.
    """

    objective: str = ""
    status: str = "open"          # open | in_progress | verifying | complete | failed | abandoned
    requirements: tuple[TaskRequirement, ...] = ()
    uncertainty: tuple[str, ...] = ()
    relevant_context: dict[str, Any] = field(default_factory=dict)
    as_of: str = field(default_factory=_utc_now_iso)

    @property
    def unsatisfied(self) -> tuple[TaskRequirement, ...]:
        return tuple(r for r in self.requirements if not r.satisfied)

    @property
    def all_satisfied(self) -> bool:
        return bool(self.requirements) and all(r.satisfied for r in self.requirements)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "status": self.status,
            "requirements": [r.to_dict() for r in self.requirements],
            "uncertainty": list(self.uncertainty),
            "relevant_context": dict(self.relevant_context),
            "as_of": self.as_of,
        }


# ---------------------------------------------------------------------------
# World model container
# ---------------------------------------------------------------------------


class WorldModel:
    """In-memory world state with append-only evidence and snapshot APIs.

    The container is thread-safe — a single instance can be shared between
    the reasoning loop and the executor that returns tool results, as long
    as both call the published update methods.

    The container does not persist between processes; durability belongs to
    Execution Log (events) and Memory (long-term facts). The World Model is
    the runtime's working copy, rebuilt at startup from those sources.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._environment = EnvironmentState()
        self._task = TaskState()
        # Local imports keep this module importable without dragging in the
        # evidence / reference modules at module load.
        from hermes.world_model.evidence import EvidenceRecord
        from hermes.world_model.reference import ReferenceBinding

        self._evidence: list[EvidenceRecord] = []
        self._references: dict[str, ReferenceBinding] = {}

    def reset(self) -> None:
        """Clear all current-world state before restoring another session."""
        with self._lock:
            self._environment = EnvironmentState()
            self._task = TaskState()
            self._evidence = []
            self._references = {}

    # ---- environment -----------------------------------------------------

    @property
    def environment(self) -> EnvironmentState:
        with self._lock:
            return self._environment

    def update_environment(self, **fields: Any) -> EnvironmentState:
        """Apply structured updates to environment state.

        Unknown keys are stored in `extra` so the caller does not have to
        extend this dataclass to use new facts.
        """
        with self._lock:
            known = {
                "active_window",
                "open_applications",
                "browser_url",
                "browser_title",
                "screen_state_id",
                "clipboard",
            }
            data = self._environment
            for key, value in fields.items():
                if key in known:
                    setattr(data, key, value)
                else:
                    data.extra[key] = value
            data.as_of = _utc_now_iso()
            return data

    # ---- task ------------------------------------------------------------

    @property
    def task(self) -> TaskState:
        with self._lock:
            return self._task

    def set_objective(
        self,
        objective: str,
        *,
        requirements: tuple[TaskRequirement, ...] = (),
        uncertainty: tuple[str, ...] = (),
        relevant_context: dict[str, Any] | None = None,
        status: str = "open",
    ) -> TaskState:
        with self._lock:
            self._task = TaskState(
                objective=objective,
                status=status,
                requirements=requirements,
                uncertainty=uncertainty,
                relevant_context=dict(relevant_context or {}),
            )
            return self._task

    def mark_requirement_satisfied(self, capability: str, evidence_id: str) -> None:
        with self._lock:
            updated: list[TaskRequirement] = []
            matched = False
            for req in self._task.requirements:
                if (
                    not matched
                    and req.capability == capability
                    and not req.satisfied
                ):
                    updated.append(
                        TaskRequirement(
                            capability=req.capability,
                            description=req.description,
                            satisfied=True,
                            evidence=(*req.evidence, evidence_id),
                        )
                    )
                    matched = True
                else:
                    updated.append(req)
            self._task = TaskState(
                objective=self._task.objective,
                status=self._task.status,
                requirements=tuple(updated),
                uncertainty=self._task.uncertainty,
                relevant_context=self._task.relevant_context,
            )

    def ensure_requirements(self, capabilities: tuple[str, ...]) -> None:
        """Add newly declared task requirements without losing progress."""
        with self._lock:
            existing = {requirement.capability for requirement in self._task.requirements}
            additions = tuple(
                TaskRequirement(capability=capability)
                for capability in capabilities
                if capability and capability not in existing
            )
            if not additions:
                return
            self._task = TaskState(
                objective=self._task.objective,
                status=self._task.status,
                requirements=(*self._task.requirements, *additions),
                uncertainty=self._task.uncertainty,
                relevant_context=self._task.relevant_context,
            )

    def reconcile_requirements(self, capabilities: tuple[str, ...]) -> None:
        """Replace the semantic requirement set while preserving matched evidence."""
        with self._lock:
            previous: dict[str, list[TaskRequirement]] = {}
            for requirement in self._task.requirements:
                previous.setdefault(requirement.capability, []).append(requirement)
            reconciled: list[TaskRequirement] = []
            for capability in capabilities:
                if not capability:
                    continue
                candidates = previous.get(capability, [])
                reconciled.append(
                    candidates.pop(0)
                    if candidates
                    else TaskRequirement(capability=capability)
                )
            self._task = TaskState(
                objective=self._task.objective,
                status=self._task.status,
                requirements=tuple(reconciled),
                uncertainty=self._task.uncertainty,
                relevant_context=self._task.relevant_context,
            )

    def set_uncertainty(self, uncertainty: tuple[str, ...]) -> None:
        with self._lock:
            self._task = TaskState(
                objective=self._task.objective,
                status=self._task.status,
                requirements=self._task.requirements,
                uncertainty=uncertainty,
                relevant_context=self._task.relevant_context,
            )

    def set_status(self, status: str) -> None:
        with self._lock:
            self._task = TaskState(
                objective=self._task.objective,
                status=status,
                requirements=self._task.requirements,
                uncertainty=self._task.uncertainty,
                relevant_context=self._task.relevant_context,
            )

    # ---- evidence --------------------------------------------------------

    @property
    def evidence(self) -> tuple["EvidenceRecord", ...]:
        with self._lock:
            return tuple(self._evidence)

    def record_evidence(self, evidence: "EvidenceRecord") -> str:
        with self._lock:
            self._evidence.append(evidence)
            return evidence.evidence_id

    def evidence_for(self, capability: str) -> tuple["EvidenceRecord", ...]:
        with self._lock:
            return tuple(e for e in self._evidence if e.capability == capability)

    def evidence_by_id(self, evidence_id: str) -> "EvidenceRecord | None":
        with self._lock:
            return next(
                (evidence for evidence in self._evidence if evidence.evidence_id == evidence_id),
                None,
            )

    # ---- references ------------------------------------------------------

    @property
    def references(self) -> dict[str, "ReferenceBinding"]:
        with self._lock:
            return dict(self._references)

    def bind_reference(self, key: str, binding: "ReferenceBinding") -> None:
        with self._lock:
            self._references[key] = binding

    def lookup_reference(self, key: str) -> "ReferenceBinding | None":
        with self._lock:
            return self._references.get(key)

    def drop_reference(self, key: str) -> bool:
        with self._lock:
            return self._references.pop(key, None) is not None

    # ---- snapshot --------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a structured snapshot for the reasoning layer."""
        with self._lock:
            return {
                "environment": self._environment.to_dict(),
                "task": self._task.to_dict(),
                "evidence": [e.to_dict() for e in self._evidence],
                "references": {
                    key: binding.to_dict()
                    for key, binding in self._references.items()
                },
                "as_of": _utc_now_iso(),
            }