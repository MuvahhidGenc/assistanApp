from __future__ import annotations

from pathlib import Path

import pytest

from hermes.context.conversational_context import ConversationalContext
from hermes.context.folder_reference import (
    extract_desktop_relative_file_path,
    extract_named_folder_name,
    resolve_folder_path_from_message,
)
from hermes.context.reference_resolver import ReferenceResolver
from hermes.mission.write_content import is_composite_file_mission, plan_composite_file_sequence
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.specific import WriteFileVerifier


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


def test_folder_reference_write_file_uses_parent_folder(resolver, tmp_path, monkeypatch):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder.resolve()))
    message = "Hermes klasörünün içine sadece deneme.txt dosyası oluştur ve içine ABC yaz."

    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: folder.resolve(),
    )

    result = resolver.resolve(message, ctx)
    assert not result.ambiguous
    assert result.intent is not None
    assert result.intent.request.name == "write_file"
    assert result.intent.request.arguments["content"] == "ABC"
    assert result.intent.request.arguments["path"].endswith("Hermes\\deneme.txt")


def test_folder_reference_does_not_fall_back_to_desktop(resolver, tmp_path, monkeypatch):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: folder.resolve(),
    )

    message = "Hermes klasörünün içine sadece deneme.txt dosyası oluştur ve içine ABC yaz."
    result = resolver.resolve(message, ConversationalContext())
    path = Path(result.intent.request.arguments["path"])
    assert path.parent.name == "Hermes"
    assert path.name == "deneme.txt"
    assert path.parent.parent != path.parent


def test_existing_folder_becomes_target_context(resolver, tmp_path, monkeypatch):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: folder.resolve(),
    )

    message = "Hermes klasöründe ikinci.txt oluştur ve içine 12345 yaz."
    result = resolver.resolve(message, ConversationalContext())
    assert result.intent.request.arguments["path"].endswith("Hermes\\ikinci.txt")
    assert result.intent.request.arguments["content"] == "12345"


def test_nested_relative_file_creation(resolver, tmp_path, monkeypatch):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: folder.resolve(),
    )

    rel = extract_desktop_relative_file_path(
        "Hermes klasörünün içine sadece deneme.txt dosyası oluştur"
    )
    assert rel is not None
    assert str(rel).endswith("Hermes\\deneme.txt") or str(rel).endswith("Hermes/deneme.txt")


def test_last_created_folder_reference(resolver, tmp_path):
    folder = tmp_path / "HermesMultiTest"
    folder.mkdir()
    ctx = ConversationalContext(
        active_folder=str(folder.resolve()),
        last_created_folder=str(folder.resolve()),
    )
    message = "Son oluşturduğun klasörün içine üçüncü.txt oluştur ve içine THREE yaz."
    result = resolver.resolve(message, ctx)
    assert not result.ambiguous
    path = Path(result.intent.request.arguments["path"])
    assert path.parent == folder.resolve()
    assert path.name == "üçüncü.txt"
    assert result.intent.request.arguments["content"] == "THREE"


def test_ambiguous_folder_reference_requires_user(resolver):
    message = "Bu klasöre dördüncü.txt yaz."
    result = resolver.resolve(message, ConversationalContext())
    assert result.ambiguous
    assert "klasor" in result.clarification.casefold()


def test_composite_skips_create_folder_when_exists(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    folder = desktop / "HermesContextTest"
    folder.mkdir(parents=True)
    monkeypatch.setattr("hermes.context.folder_reference.Path.home", lambda: tmp_path)

    goal = (
        "Masaüstünde HermesContextTest klasörü oluştur. İçine test.txt dosyası oluştur "
        "ve içine Merhaba Dünya yaz."
    )
    steps = plan_composite_file_sequence(goal)
    assert len(steps) == 1
    assert steps[0].request.name == "write_file"
    assert steps[0].request.arguments["path"].endswith("HermesContextTest\\test.txt")
    assert steps[0].request.arguments["content"] == "Merhaba Dünya"


def test_named_folder_message_is_not_composite_create():
    message = "Hermes klasörünün içine deneme.txt oluştur ve içine ABC yaz."
    assert is_composite_file_mission(message) is False


@pytest.mark.asyncio
async def test_verification_checks_expected_parent_directory(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    wrong_parent = desktop / "deneme.txt"
    wrong_parent.parent.mkdir(parents=True, exist_ok=True)
    wrong_parent.write_text("ABC", encoding="utf-8")

    monkeypatch.setattr(
        "hermes.tools.verifiers.specific.resolve_user_path",
        lambda raw: wrong_parent.resolve(),
    )

    verifier = WriteFileVerifier()
    result = await verifier.verify(
        VerifierContext(
            tool_name="write_file",
            tool_arguments={"path": "Hermes/deneme.txt", "content": "ABC"},
            execution_success=True,
            execution_output={"path": str(wrong_parent)},
        )
    )
    assert result.status.value == "failed"
    assert result.details.get("reason") == "unexpected_parent_directory"


def test_extract_named_folder_name_variants():
    assert extract_named_folder_name("Hermes klasörünün içine") == "Hermes"
    assert extract_named_folder_name("Hermes klasöründe ikinci.txt") == "Hermes"
    assert extract_named_folder_name("Hermes klasörüne dosya") == "Hermes"


def test_contextual_folder_resolves_active_folder(tmp_path):
    folder = tmp_path / "Active"
    folder.mkdir()
    resolved = resolve_folder_path_from_message(
        "Bu klasöre test.txt yaz",
        active_folder=str(folder.resolve()),
    )
    assert resolved == str(folder.resolve())
