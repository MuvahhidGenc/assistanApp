"""V3.6 static and runtime isolation acceptance tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import hermes


ROOT = Path(hermes.__file__).resolve().parents[2]


def test_v3_bootstrap_and_voice_do_not_import_v2_semantic_stacks():
    code = """
import json
import sys
import tempfile
from pathlib import Path
from hermes.app.bootstrap import HermesApplication
from hermes.runtime.bootstrap import build_v3_application
from hermes.voice.assistant import VoiceAssistant
build_v3_application(server=object(), log_dir=Path(tempfile.mkdtemp()))
forbidden = sorted(
    name for name in sys.modules
    if name == "hermes.intent"
    or name.startswith("hermes.intent.")
    or name == "hermes.agent"
    or name.startswith("hermes.agent.")
    or name in {
        "hermes.mission.engine",
        "hermes.mission.planner",
        "hermes.mission.selection",
    }
)
print(json.dumps(forbidden))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout.strip()) == []


def test_production_entrypoints_name_only_v3_orchestrator():
    app_bootstrap = (ROOT / "src/hermes/app/bootstrap.py").read_text(
        encoding="utf-8"
    )
    voice = (ROOT / "src/hermes/voice/assistant.py").read_text(encoding="utf-8")
    main = (ROOT / "src/hermes/main.py").read_text(encoding="utf-8")

    assert "build_v3_application" in app_bootstrap
    assert "hermes.agent" not in app_bootstrap
    assert "V3Orchestrator" in voice
    assert "AgentOrchestrator" not in voice
    assert "hermes.app.bootstrap" in main


def test_v3_runtime_has_no_legacy_semantic_imports():
    forbidden = (
        "hermes.agent",
        "hermes.intent",
        "hermes.mission.engine",
        "hermes.mission.planner",
        "hermes.mission.selection",
    )
    runtime_files = [
        ROOT / "src/hermes/runtime/bootstrap.py",
        ROOT / "src/hermes/runtime/orchestrator.py",
        ROOT / "src/hermes/runtime/executor.py",
        ROOT / "src/hermes/reasoning/runtime.py",
        ROOT / "src/hermes/reasoning/transport.py",
    ]
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{token} is reachable through {path}"


def test_package_initializers_have_no_legacy_import_side_effects():
    for relative in (
        "src/hermes/agent/__init__.py",
        "src/hermes/intent/__init__.py",
        "src/hermes/context/__init__.py",
        "src/hermes/mission/__init__.py",
        "src/hermes/screen/__init__.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "\nfrom hermes." not in source


def test_frozen_client_excludes_retired_semantic_orchestrators():
    spec = (ROOT / "hermes-client.spec").read_text(encoding="utf-8")
    for module in (
        '"hermes.agent"',
        '"hermes.intent"',
        '"hermes.mission.engine"',
        '"hermes.mission.planner"',
        '"hermes.mission.selection"',
    ):
        assert module in spec


def test_authoritative_v2_runtime_and_voice_fallback_are_removed():
    assert not (ROOT / "src/hermes/agent/orchestrator.py").exists()


def test_voice_presentation_cannot_reroute_user_intent():
    source = (ROOT / "src/hermes/voice/response_synthesizer.py").read_text(
        encoding="utf-8"
    )
    for token in (
        "GoalRouter",
        "ReferenceResolver",
        "local_intent",
        "file_intent",
        "guess_tool",
    ):
        assert token not in source
