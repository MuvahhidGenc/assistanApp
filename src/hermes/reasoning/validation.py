"""Strict validation for decisions received from an external reasoner."""

from __future__ import annotations

from collections import Counter
from typing import Any


_KINDS = {
    "action",
    "observation_request",
    "user_question",
    "complete",
    "re_reason",
}
_TRUSTED_EVIDENCE_SOURCES = {"observation", "verifier"}


class DecisionContractError(ValueError):
    """An external reasoning response violated the V3 decision contract."""


def validate_decision_payload(
    payload: Any,
    *,
    available_capabilities: tuple[dict[str, Any], ...],
    world_snapshot: dict[str, Any],
    correlation_id: str = "",
) -> dict[str, Any]:
    """Validate one decision without repairing, defaulting, or interpreting it."""
    if not isinstance(payload, dict):
        raise DecisionContractError("Reasoning decision must be a JSON object")

    kind = _required_text(payload, "kind")
    if kind not in _KINDS:
        raise DecisionContractError(f"Unknown decision kind: {kind!r}")

    contracts = {
        str(item.get("name") or "").strip(): item
        for item in available_capabilities
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    required = _required_capabilities(payload, contracts)
    _validate_requirement_transition(
        required,
        world_snapshot,
        kind=kind,
        action_capability=(
            str(payload.get("capability") or "").strip()
            if kind == "action"
            else ""
        ),
    )

    if kind == "action":
        capability = _required_text(payload, "capability")
        _known_capability(capability, contracts, field="capability")
        if capability not in required:
            raise DecisionContractError(
                "Action capability must be present in required_capabilities"
            )
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            raise DecisionContractError("Action arguments must be an object")
        _validate_required_inputs(contracts[capability], arguments)
    elif kind == "observation_request":
        observation_type = _required_text(payload, "observation_type")
        contract = _known_capability(
            observation_type, contracts, field="observation_type"
        )
        if str(contract.get("risk_level") or "") != "read_only":
            raise DecisionContractError(
                "observation_type must name a registered read-only capability"
            )
        parameters = payload.get("parameters", {})
        if not isinstance(parameters, dict):
            raise DecisionContractError(
                "Observation request parameters must be an object"
            )
        observation_arguments = dict(parameters)
        _apply_observation_target(
            observation_arguments,
            str(payload.get("target") or "").strip(),
            contract,
        )
        _validate_required_inputs(contract, observation_arguments)
    elif kind == "user_question":
        _required_text(payload, "question")
        options = payload.get("options", [])
        if not isinstance(options, list):
            raise DecisionContractError("User question options must be an array")
    elif kind == "complete":
        _required_text(payload, "summary")
        _validate_complete_evidence(
            payload,
            world_snapshot=world_snapshot,
            correlation_id=correlation_id,
        )
    elif kind == "re_reason":
        reason = payload.get("reason", "")
        if not isinstance(reason, str):
            raise DecisionContractError("Re-reason reason must be a string")

    memory_facts = payload.get("memory_facts", [])
    if not isinstance(memory_facts, list):
        raise DecisionContractError("memory_facts must be an array")
    return payload


def _required_capabilities(
    payload: dict[str, Any],
    contracts: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    if "required_capabilities" not in payload:
        raise DecisionContractError("Missing required_capabilities")
    raw = payload["required_capabilities"]
    if raw is None:
        raise DecisionContractError("required_capabilities cannot be null")
    if not isinstance(raw, list):
        raise DecisionContractError("required_capabilities must be an array")

    result: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise DecisionContractError(
                "required_capabilities entries must be non-empty strings"
            )
        capability = value.strip()
        _known_capability(capability, contracts, field="required_capabilities")
        result.append(capability)
    return tuple(result)


def _known_capability(
    capability: str,
    contracts: dict[str, dict[str, Any]],
    *,
    field: str,
) -> dict[str, Any]:
    contract = contracts.get(capability)
    if contract is None:
        raise DecisionContractError(
            f"{field} contains an unknown capability: {capability!r}"
        )
    return contract


def _validate_requirement_transition(
    required: tuple[str, ...],
    world_snapshot: dict[str, Any],
    *,
    kind: str,
    action_capability: str,
) -> None:
    task = world_snapshot.get("task")
    if not isinstance(task, dict):
        return
    existing_items = task.get("requirements")
    if not isinstance(existing_items, list) or not existing_items:
        return
    existing = tuple(
        str(item.get("capability") or "").strip()
        for item in existing_items
        if isinstance(item, dict) and str(item.get("capability") or "").strip()
    )
    if Counter(existing) == Counter(required):
        return

    uncertainty = task.get("uncertainty")
    added = Counter(required) - Counter(existing)
    if (
        kind == "action"
        and action_capability
        and isinstance(uncertainty, list)
        and uncertainty
        and set(added) <= {action_capability}
    ):
        return
    raise DecisionContractError(
        "required_capabilities changed within the current task"
    )


def _validate_complete_evidence(
    payload: dict[str, Any],
    *,
    world_snapshot: dict[str, Any],
    correlation_id: str,
) -> None:
    raw_ids = payload.get("evidence_ids")
    if not isinstance(raw_ids, list):
        raise DecisionContractError("Complete evidence_ids must be an array")
    if any(not isinstance(value, str) or not value.strip() for value in raw_ids):
        raise DecisionContractError(
            "Complete evidence_ids entries must be non-empty strings"
        )

    task = world_snapshot.get("task")
    requirements = (
        task.get("requirements", [])
        if isinstance(task, dict)
        else []
    )
    evidence_items = world_snapshot.get("evidence")
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_items
        if isinstance(item, dict) and str(item.get("evidence_id") or "")
    } if isinstance(evidence_items, list) else {}
    requirement_evidence = {
        str(evidence_id)
        for item in requirements
        if isinstance(item, dict)
        for evidence_id in item.get("evidence", [])
    }

    for evidence_id in raw_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            raise DecisionContractError(
                f"Complete cites unknown evidence: {evidence_id}"
            )
        if requirements and evidence_id not in requirement_evidence:
            raise DecisionContractError(
                f"Complete evidence is unrelated to requirements: {evidence_id}"
            )
        if not requirements and correlation_id and (
            str(evidence.get("correlation_id") or "") != correlation_id
        ):
            raise DecisionContractError(
                f"Complete evidence is stale for this turn: {evidence_id}"
            )
        source = str(evidence.get("source") or "")
        if source not in _TRUSTED_EVIDENCE_SOURCES:
            raise DecisionContractError(
                f"Complete evidence has untrusted source: {evidence_id}"
            )
        data = evidence.get("data")
        if source == "verifier" and (
            not isinstance(data, dict) or data.get("status") != "verified"
        ):
            raise DecisionContractError(
                f"Complete cites unverified evidence: {evidence_id}"
            )


def _validate_required_inputs(
    contract: dict[str, Any],
    arguments: dict[str, Any],
) -> None:
    schema = contract.get("input_schema")
    if not isinstance(schema, dict):
        return
    required = schema.get("required", [])
    if not isinstance(required, list):
        return
    missing = [
        str(field)
        for field in required
        if field not in arguments
        or arguments[field] is None
        or (isinstance(arguments[field], str) and not arguments[field].strip())
    ]
    if missing:
        raise DecisionContractError(
            "Missing required capability arguments: " + ", ".join(missing)
        )


def _apply_observation_target(
    arguments: dict[str, Any],
    target: str,
    contract: dict[str, Any],
) -> None:
    if not target:
        return
    schema = contract.get("input_schema")
    if not isinstance(schema, dict):
        return
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    if "target" in properties:
        arguments.setdefault("target", target)
    elif "path" in properties:
        arguments.setdefault("path", target)
    else:
        required = schema.get("required", [])
        if isinstance(required, list) and len(required) == 1:
            arguments.setdefault(str(required[0]), target)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DecisionContractError(f"Missing or empty required field: {field}")
    return value.strip()


__all__ = ["DecisionContractError", "validate_decision_payload"]
