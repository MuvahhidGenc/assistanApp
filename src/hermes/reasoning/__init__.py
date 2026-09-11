"""V3 Reasoning Runtime — the semantic decision owner.

The runtime is the *only* layer that decides what Hermes should do next.
It does so by:

  1. reading the World Model and the recent Execution Log,
  2. handing the resulting context to the server-side LLM,
  3. parsing the LLM's structured reply into a typed `Decision`,
  4. returning the decision to the orchestrator.

The runtime does **not**:

  * inspect user language with regex,
  * route a message to a capability,
  * execute tools,
  * call Policy/Approval (the orchestrator does, before executing),
  * maintain a workflow or "plan" object that persists across turns.

The runtime is intentionally a thin shell: every decision is the LLM's
answer to "given the world as we know it and what we just did, what next?".
Re-reasoning is a free call — there is no plan to commit.
"""

from hermes.reasoning.decision import (
    Action,
    Complete,
    Decision,
    DecisionKind,
    MemoryFact,
    ObservationRequest,
    ReReason,
    UserQuestion,
)
from hermes.reasoning.runtime import ReasoningRuntime
from hermes.reasoning.transport import (
    LlmReasoningClient,
    ReasoningPrompt,
    ReasoningReply,
)

__all__ = [
    "Action",
    "Complete",
    "Decision",
    "DecisionKind",
    "LlmReasoningClient",
    "MemoryFact",
    "ObservationRequest",
    "ReReason",
    "ReasoningPrompt",
    "ReasoningReply",
    "ReasoningRuntime",
    "UserQuestion",
]