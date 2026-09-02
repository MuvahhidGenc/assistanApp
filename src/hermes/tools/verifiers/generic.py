from __future__ import annotations

from typing import Any

from hermes.tools.verifiers.base import Observation, VerificationResult, VerificationStatus
from hermes.tools.verifiers.context import VerifierContext


class GenericVerifier:
    """Fallback when no dedicated verifier exists or specialized verifier returns unknown."""

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        if not ctx.verification_required:
            return VerificationResult(
                status=VerificationStatus.NOT_REQUIRED,
                method="generic_not_required",
                details={"reason": "verification_not_required"},
            )

        if not ctx.execution_success:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="generic_execution",
                details={"reason": "execution_failed", "error": ctx.execution_error},
                observation=Observation(
                    source="execution_output",
                    data={"success": False, "error": ctx.execution_error},
                ),
            )

        method = ctx.verification_method or "generic_output"
        output = ctx.execution_output
        observation = Observation(
            source="execution_output",
            data={"output_type": type(output).__name__, "output": output},
        )

        if method == "output_present":
            if output:
                return VerificationResult(
                    status=VerificationStatus.VERIFIED,
                    method="generic_output_present",
                    details={"reason": "output_present"},
                    observation=observation,
                )
            return VerificationResult(
                status=VerificationStatus.FAILED,
                method="generic_output_present",
                details={"reason": "output_missing"},
                observation=observation,
            )

        if method == "manual_review":
            return VerificationResult(
                status=VerificationStatus.NOT_REQUIRED,
                method="generic_manual_review",
                details={"reason": "manual_review_deferred"},
                observation=observation,
            )

        expected = ctx.expected_result
        if expected and output is not None:
            output_text = str(output).casefold()
            if expected.casefold() in output_text:
                return VerificationResult(
                    status=VerificationStatus.VERIFIED,
                    method="generic_expected_result",
                    details={"matched_expected_result": expected[:200]},
                    observation=observation,
                )

        if isinstance(output, dict) and output:
            if output.get("exists") is True or output.get("verified") is True:
                return VerificationResult(
                    status=VerificationStatus.UNKNOWN,
                    method="generic_tool_output_hint",
                    details={
                        "reason": "tool_claims_success_but_unconfirmed",
                        "hint_keys": list(output.keys())[:10],
                    },
                    observation=observation,
                )
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="generic_structured_output",
                details={"reason": "structured_output_unverified", "keys": list(output.keys())[:10]},
                observation=observation,
            )

        if output:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method="generic_raw_output",
                details={"reason": "non_empty_output_unverified"},
                observation=observation,
            )

        return VerificationResult(
            status=VerificationStatus.UNKNOWN,
            method="generic_empty",
            details={"reason": "no_observable_state"},
            observation=observation,
        )
