"""Capability ↔ verifier binding.

The binding answers a single question: "for this capability, what verifier
should the runtime call to confirm the effect?". It is *not* a router — if
no binding exists, the runtime falls back to the contract's
`verification.method` and then to the generic verifier.

The verifier implementations live in `hermes.tools.verifiers`. This module
only records which verifier is responsible for which capability, and lets
the registry expose that lookup.
"""

from __future__ import annotations

from dataclasses import dataclass

from hermes.tools.verifiers.base import BaseVerifier


@dataclass(frozen=True)
class CapabilityVerifierBinding:
    """A single capability's verifier.

    `verifier` is the V2 verifier instance; `capability` is the capability
    name; `method` overrides the contract's verification method when the
    verifier names a specific strategy. If `method` is empty the registry
    uses the contract's default.
    """

    capability: str
    verifier: BaseVerifier
    method: str = ""

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self.verifier.tool_names)