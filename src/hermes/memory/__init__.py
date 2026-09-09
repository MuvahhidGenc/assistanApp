"""V3 Memory — three layers, each with a clear purpose.

Memory is **not** the World Model. The World Model holds the runtime's
working view of the world — environment, task, evidence, references —
and is rebuilt per turn from facts the user and tools have established.
Memory holds what the user has *over time*, with explicit lifetimes.

Layers:

  * `session_memory`  — the current conversation turn-by-turn. Discarded
                        when the session ends (or persisted by policy).
  * `episodic_memory` — past tasks and outcomes. Useful when the runtime
                        wants to recall "did we do this last week?"
  * `long_term`       — long-lived facts about the user (preferences,
                        name, language, environment quirks). The runtime
                        must never write a credential or a secret here.

Every memory layer has the same shape:

  * `record(...)` appends a fact.
  * `query(...)` retrieves facts.
  * `forget(...)` removes a fact (or marks it expired).

Memory layers never *decide* anything. They are pure stores with retrieval.
The reasoning layer decides what to record.
"""

from hermes.memory.episodic import EpisodicMemory, EpisodicRecord
from hermes.memory.long_term import LongTermMemory, LongTermFact
from hermes.memory.runtime import RuntimeMemory
from hermes.memory.security import MemoryPersistenceError, MemorySecurityError
from hermes.memory.session import SessionMemory, SessionTurn, _scrub_session_text

__all__ = [
    "EpisodicMemory",
    "EpisodicRecord",
    "LongTermFact",
    "LongTermMemory",
    "MemoryPersistenceError",
    "MemorySecurityError",
    "RuntimeMemory",
    "SessionMemory",
    "SessionTurn",
    "_scrub_session_text",
]