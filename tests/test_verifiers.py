from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.mission.models import MissionStep
from hermes.mission.engine import MissionEngine
from hermes.mission.models import Mission, MissionStep, MissionStepStatus, StepAction
from hermes.mission.store import MissionStore
from hermes.mission.models import MissionStep
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.generic import GenericVerifier
from hermes.tools.verifiers.registry import VerifierRegistry, create_default_verifier_registry
from hermes.tools.verifiers.specific import (
    CreateFolderVerifier,
    GitCloneVerifier,
    InstallProgramVerifier,
    OpenUrlVerifier,
    SetDnsVerifier,
    WriteFileVerifier,
)
from hermes.tools.verifiers.base import VerificationStatus


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.fixture
def mission_root(tmp_path, monkeypatch):
    root = tmp_path / "missions"
    index_path = root / "index.json"
    monkeypatch.setattr("hermes.mission.store.missions_dir", lambda: root)
    monkeypatch.setattr("hermes.mission.store.missions_index_path", lambda: index_path)
    monkeypatch.setattr(
        "hermes.mission.store.ensure_user_dirs",
        lambda: root.mkdir(parents=True, exist_ok=True),
    )
    return root


def _ctx(
    tool_name: str,
    *,
    success: bool = True,
    output=None,
    arguments=None,
    step=None,
    observe=None,
):
    return VerifierContext(
        tool_name=tool_name,
        tool_arguments=arguments or {},
        execution_success=success,
        execution_output=output,
        execution_error=None if success else "failed",
        step=step,
        run_id="test-run",
        observe_tool=observe,
    )


@pytest.mark.asyncio
async def test_git_clone_verification_success(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    (target / ".git").mkdir()
    verifier = GitCloneVerifier()
    result = await verifier.verify(
        _ctx(
            "git_clone",
            output={"path": str(target), "repo_url": "https://github.com/a/b.git"},
            arguments={"repo_url": "https://github.com/a/b.git", "target_dir": str(target)},
        )
    )
    assert result.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_git_clone_verification_failure(tmp_path):
    target = tmp_path / "missing"
    verifier = GitCloneVerifier()
    result = await verifier.verify(
        _ctx(
            "git_clone",
            output={"path": str(target)},
            arguments={"repo_url": "https://github.com/a/b.git", "target_dir": str(target)},
        )
    )
    assert result.status == VerificationStatus.FAILED


@pytest.mark.asyncio
async def test_install_verification_success_with_observe():
    async def observe(tool_name, arguments, run_id):
        assert tool_name == "list_installed_programs"
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={
                "count": 1,
                "programs": [{"DisplayName": "Google Chrome", "DisplayVersion": "1.0"}],
            },
        )

    verifier = InstallProgramVerifier()
    result = await verifier.verify(
        _ctx(
            "install_program",
            output={"package": "chrome"},
            arguments={"package": "chrome"},
            observe=observe,
        )
    )
    assert result.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_install_verification_failure_with_observe():
    async def observe(tool_name, arguments, run_id):
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"count": 0, "programs": []},
        )

    verifier = InstallProgramVerifier()
    result = await verifier.verify(
        _ctx(
            "install_program",
            output={"package": "chrome"},
            arguments={"package": "chrome"},
            observe=observe,
        )
    )
    assert result.status == VerificationStatus.FAILED


@pytest.mark.asyncio
async def test_dns_verification_success():
    async def observe(tool_name, arguments, run_id):
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={
                "dns_servers": [{"InterfaceAlias": "Wi-Fi", "ServerAddresses": ["8.8.8.8", "8.8.4.4"]}],
                "adapters": [],
            },
        )

    verifier = SetDnsVerifier()
    result = await verifier.verify(
        _ctx(
            "set_dns",
            output={"adapter": "Wi-Fi", "servers": ["8.8.8.8", "8.8.4.4"]},
            arguments={"preset": "google"},
            observe=observe,
        )
    )
    assert result.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_dns_verification_failure():
    async def observe(tool_name, arguments, run_id):
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"dns_servers": [{"ServerAddresses": ["1.1.1.1"]}]},
        )

    verifier = SetDnsVerifier()
    result = await verifier.verify(
        _ctx(
            "set_dns",
            output={"adapter": "Wi-Fi"},
            arguments={"preset": "google"},
            observe=observe,
        )
    )
    assert result.status == VerificationStatus.FAILED


@pytest.mark.asyncio
async def test_create_folder_verification(tmp_path):
    folder = tmp_path / "demo"
    folder.mkdir()
    verifier = CreateFolderVerifier()
    result = await verifier.verify(
        _ctx("create_folder", output={"path": str(folder)}, arguments={"path": str(folder)})
    )
    assert result.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_write_file_verification(tmp_path, monkeypatch):
    from hermes.tools.windows import file_tools

    monkeypatch.setattr(file_tools, "resolve_user_path", lambda raw: Path(raw))

    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    verifier = WriteFileVerifier()
    result = await verifier.verify(
        _ctx(
            "write_file",
            output={"path": str(target), "exists": True},
            arguments={"path": str(target), "content": "hello"},
        )
    )
    assert result.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_open_url_verification_success():
    verifier = OpenUrlVerifier()
    result = await verifier.verify(
        _ctx(
            "open_url",
            output={"url": "https://example.com", "opened": True},
            arguments={"url": "https://example.com"},
        )
    )
    assert result.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_generic_verification_unknown():
    verifier = GenericVerifier()
    result = await verifier.verify(_ctx("echo", output={"message": "hi"}, arguments={"message": "hi"}))
    assert result.status == VerificationStatus.UNKNOWN


@pytest.mark.asyncio
async def test_generic_verification_output_present():
    verifier = GenericVerifier()
    step = MissionStep(step_id="s", title="t", verification={"required": True, "method": "output_present"})
    result = await verifier.verify(
        _ctx("get_system_info", output={"hostname": "PC"}, step=step)
    )
    assert result.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_verification_timeout(registry):
    class SlowVerifier(GitCloneVerifier):
        async def verify(self, ctx):
            await asyncio.sleep(2)
            return await super().verify(ctx)

    reg = VerifierRegistry()
    reg.register(SlowVerifier())
    ctx = _ctx("git_clone", output={"path": "/tmp/x"})
    ctx.timeout_seconds = 0.05
    result = await reg.verify(ctx)
    assert result.status == VerificationStatus.UNKNOWN
    assert result.details.get("reason") == "verification_timeout"


@pytest.mark.asyncio
async def test_execution_success_verification_failure(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission("Clone test")
    target = mission_root / "repo"
    mission.steps = [
        MissionStep(
            step_id="clone",
            title="Clone repo",
            action=StepAction.TOOL,
            tool_name="git_clone",
            tool_arguments={"repo_url": "https://github.com/a/b.git", "target_dir": str(target)},
            verification={"required": True},
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = __import__(
        "hermes.security.policy_engine", fromlist=["PolicyDecision"]
    ).PolicyDecision.ALLOW
    executor.execute_tool_call = AsyncMock(
        return_value=ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"path": str(target), "repo_url": "https://github.com/a/b.git"},
        )
    )

    engine = MissionEngine(store, registry, executor)
    result = await engine.run(mission.mission_id, MagicMock())
    assert result.handled is True
    assert result.success is False
    loaded = store.load(mission.mission_id)
    assert loaded.steps[0].status == MissionStepStatus.VERIFICATION_FAILED
    assert loaded.recovery_attempts


@pytest.mark.asyncio
async def test_resumed_mission_verification(mission_root, registry, tmp_path):
    store = MissionStore()
    folder = tmp_path / "cached"
    folder.mkdir()
    mission = store.create_mission("Resume verify")
    mission.steps = [
        MissionStep(
            step_id="folder",
            title="Folder",
            action=StepAction.TOOL,
            tool_name="create_folder",
            tool_arguments={"path": str(folder)},
            verification={"required": True},
            status=MissionStepStatus.PENDING,
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = __import__(
        "hermes.security.policy_engine", fromlist=["PolicyDecision"]
    ).PolicyDecision.ALLOW
    executor.execute_tool_call = AsyncMock(
        return_value=ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"path": str(folder)},
        )
    )

    engine = MissionEngine(store, registry, executor)
    result = await engine.run(mission.mission_id, MagicMock())
    assert result.success is True
    loaded = store.load(mission.mission_id)
    assert loaded.steps[0].verification_status == "verified"


def test_default_registry_has_specific_verifiers():
    reg = create_default_verifier_registry()
    assert reg.get("git_clone") is not None
    assert reg.get("install_program") is not None
    assert reg.get("set_dns") is not None
    assert reg.get("write_file") is not None
    assert reg.get("create_folder") is not None
    assert reg.get("open_url") is not None
    assert reg.get("read_file") is not None
    assert reg.get("open_path") is not None
    assert reg.get("open_app") is not None


@pytest.mark.asyncio
async def test_read_file_verifier_rejects_a_lying_success(tmp_path):
    missing = tmp_path / "ghost.txt"
    from hermes.tools.verifiers.specific import ReadFileVerifier

    result = await ReadFileVerifier().verify(
        _ctx(
            "read_file",
            output={"path": str(missing), "content": "forged", "exists": True},
            arguments={"path": str(missing)},
        )
    )
    assert result.status == VerificationStatus.FAILED
    assert result.method == "read_file_filesystem"


@pytest.mark.asyncio
async def test_read_file_verifier_confirms_a_real_file(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )
    from hermes.tools.verifiers.specific import ReadFileVerifier

    result = await ReadFileVerifier().verify(
        _ctx(
            "read_file",
            output={"path": str(target), "content": "hello", "exists": True},
            arguments={"path": str(target)},
        )
    )
    assert result.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_open_path_verifier_rejects_a_missing_path(tmp_path):
    missing = tmp_path / "nope"
    from hermes.tools.verifiers.specific import OpenPathVerifier

    result = await OpenPathVerifier().verify(
        _ctx(
            "open_path",
            output={"path": str(missing), "verified": True},
            arguments={"path": str(missing)},
        )
    )
    assert result.status == VerificationStatus.FAILED
    assert result.method == "open_path_action"


@pytest.mark.asyncio
async def test_open_path_verifier_confirms_existing_path(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    from hermes.tools.verifiers.specific import OpenPathVerifier

    result = await OpenPathVerifier().verify(
        _ctx(
            "open_path",
            output={"path": str(folder), "verified": True},
            arguments={"path": str(folder)},
        )
    )
    assert result.status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_open_app_verifier_rejects_a_missing_binary(tmp_path):
    from hermes.tools.verifiers.specific import OpenAppVerifier

    result = await OpenAppVerifier().verify(
        _ctx(
            "open_app",
            output={
                "app": "chrome",
                "path": str(tmp_path / "chrome.exe"),
                "verified": True,
                "window_title": "Chrome",
            },
            arguments={"app": "chrome"},
        )
    )
    assert result.status == VerificationStatus.FAILED
    assert result.details.get("reason") == "app_binary_missing"


@pytest.mark.asyncio
async def test_open_app_verifier_does_not_trust_self_reported_window(tmp_path, monkeypatch):
    binary = tmp_path / "app.exe"
    binary.write_bytes(b"mz")
    monkeypatch.setattr(
        "hermes.tools.windows.input_backend.find_app_window_title",
        lambda app, **kwargs: None,
    )
    from hermes.tools.verifiers.specific import OpenAppVerifier

    result = await OpenAppVerifier().verify(
        _ctx(
            "open_app",
            output={
                "app": "notepad",
                "path": str(binary),
                "reused": True,
                "verified": True,
                "window_title": "Notepad",
            },
            arguments={"app": "notepad"},
        )
    )
    assert result.status == VerificationStatus.UNKNOWN
