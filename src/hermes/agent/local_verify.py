from __future__ import annotations

from pathlib import Path

from hermes.server.models import ToolResultPayload
from hermes.tools.verifiers.base import VerificationStatus
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.specific import WriteFileVerifier
from hermes.tools.windows.file_tools import resolve_user_path


def validate_write_parent_directory(
    file_path: str,
    expected_folder: str | None,
) -> tuple[bool, str]:
    """Ensure write_file target lives under the expected parent folder."""
    if not expected_folder:
        return True, ""
    try:
        target = resolve_user_path(str(file_path))
        expected_parent = Path(expected_folder).expanduser()
        if not expected_parent.is_absolute():
            expected_parent = (Path.home() / "Desktop" / expected_parent).resolve()
        else:
            expected_parent = expected_parent.resolve()
    except (OSError, ValueError) as exc:
        return False, str(exc)
    if target.parent.resolve() != expected_parent:
        return (
            False,
            f"hedef {target.parent.name}, beklenen {expected_parent.name}",
        )
    return True, ""


async def verify_create_folder_result(
    result: ToolResultPayload,
    arguments: dict[str, object],
) -> ToolResultPayload:
    from hermes.tools.verifiers.specific import CreateFolderVerifier

    verifier = CreateFolderVerifier()
    ctx = VerifierContext(
        tool_name="create_folder",
        tool_arguments=dict(arguments),
        execution_success=bool(result.success),
        execution_output=result.output,
        execution_error=result.error,
    )
    verification = await verifier.verify(ctx)
    output = dict(result.output) if isinstance(result.output, dict) else {}
    output["verified"] = verification.status == VerificationStatus.VERIFIED
    if verification.status != VerificationStatus.VERIFIED:
        output["verification_failed"] = True
        output["verification_details"] = verification.details
    else:
        output.pop("verification_failed", None)
    return ToolResultPayload(
        tool_call_id=result.tool_call_id,
        success=bool(result.success) and output.get("verified") is True,
        output=output,
        error=result.error if output.get("verified") else (
            result.error
            or str((verification.details or {}).get("reason") or "verification_failed")
        ),
    )


async def verify_rename_path_result(
    result: ToolResultPayload,
    arguments: dict[str, object],
) -> ToolResultPayload:
    if not result.success:
        return result
    output = dict(result.output) if isinstance(result.output, dict) else {}
    source = str(output.get("source") or arguments.get("path") or "")
    destination = str(output.get("destination") or "")
    if not destination and source and arguments.get("new_name"):
        destination = str(Path(source).with_name(str(arguments["new_name"])))
    verified = bool(destination) and Path(destination).exists() and not (
        source and Path(source).exists()
    )
    output["verified"] = verified
    if destination:
        output["destination"] = destination
    if source:
        output["source"] = source
    if not verified:
        output["verification_failed"] = True
    return ToolResultPayload(
        tool_call_id=result.tool_call_id,
        success=bool(result.success) and verified,
        output=output,
        error=result.error,
    )


async def verify_write_file_result(
    result: ToolResultPayload,
    arguments: dict[str, object],
) -> ToolResultPayload:
    verifier = WriteFileVerifier()
    ctx = VerifierContext(
        tool_name="write_file",
        tool_arguments=dict(arguments),
        execution_success=bool(result.success),
        execution_output=result.output,
        execution_error=result.error,
    )
    verification = await verifier.verify(ctx)
    output = dict(result.output) if isinstance(result.output, dict) else {}
    observed_path = None
    if verification.observation and isinstance(verification.observation.data, dict):
        observed_path = verification.observation.data.get("path")
    if observed_path:
        output["path"] = observed_path
    elif arguments.get("path") and "path" not in output:
        output["path"] = str(arguments["path"])
    output["verified"] = verification.status == VerificationStatus.VERIFIED
    if verification.status != VerificationStatus.VERIFIED:
        output["verification_failed"] = True
        output["verification_details"] = verification.details
    else:
        output.pop("verification_failed", None)
    return ToolResultPayload(
        tool_call_id=result.tool_call_id,
        success=bool(result.success) and output.get("verified") is True,
        output=output,
        error=result.error if output.get("verified") else (
            result.error
            or str((verification.details or {}).get("reason") or "verification_failed")
        ),
    )
