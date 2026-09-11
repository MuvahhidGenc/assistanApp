"""V3 World Model — the runtime's view of reality.

The World Model is what ReasoningRuntime *knows* about the world, distinct
from what the user *asked* and what the system *did*. It is the canonical
store for:

  * environment state (active window, screen state, browser state, …),
  * task state (current objective, completed requirements, uncertainty),
  * evidence (observations and their sources, with provenance),
  * reference state (resolved "this file" / "that window" / "the site we
    opened" — the bindings the runtime and user rely on).

The World Model is **not**:

  * an LLM or a model — it is plain data the reasoning layer consumes,
  * a routing layer — it never decides what to do,
  * a memory of every past interaction — Memory handles that,
  * a planner — ReasoningRuntime reasons over it.

It separates *what is known* from *what was inferred*. Every claim about
the world carries a source: "the tool reported X" is different from "we
observed X" is different from "the user said X". The reasoning layer
trusts claims in proportion to their source.
"""

from hermes.world_model.evidence import (
    EvidenceRecord,
    EvidenceSource,
)
from hermes.world_model.state import (
    EnvironmentState,
    TaskRequirement,
    TaskState,
    WorldModel,
)
from hermes.world_model.reference import (
    ReferenceBinding,
    ReferenceKind,
    ReferenceResolver,
)

__all__ = [
    "EnvironmentState",
    "EvidenceRecord",
    "EvidenceSource",
    "ReferenceBinding",
    "ReferenceKind",
    "ReferenceResolver",
    "TaskRequirement",
    "TaskState",
    "WorldModel",
]