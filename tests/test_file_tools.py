from __future__ import annotations

from pathlib import Path

import pytest

from hermes.tools.windows.file_tools import (
    CreateWordDocumentTool,
    DeletePathTool,
    ListDirectoryTool,
    WriteFileTool,
    resolve_user_path,
)


def test_resolve_user_path_desktop_relative():
    path = resolve_user_path("Hermes2/tevhid.docx")
    assert path.name == "tevhid.docx"
    assert path.parent.name == "Hermes2"
    assert "Desktop" in str(path)


@pytest.mark.asyncio
async def test_write_and_list_and_delete(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.Path.home",
        lambda: tmp_path,
    )

    write = WriteFileTool()
    created = await write.execute(path="Hermes2/not.txt", content="tevhid")
    assert created.success
    assert Path(created.output["path"]).is_file()

    listing = ListDirectoryTool()
    listed = await listing.execute(path="Hermes2")
    assert listed.success
    names = [e["name"] for e in listed.output["entries"]]
    assert "not.txt" in names

    delete = DeletePathTool()
    removed = await delete.execute(path="Hermes2/not.txt")
    assert removed.success
    assert removed.output["exists_after"] is False


@pytest.mark.asyncio
async def test_create_word_document(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.Path.home",
        lambda: tmp_path,
    )

    tool = CreateWordDocumentTool()
    result = await tool.execute(
        path="Hermes2/tevhid.docx",
        title="Tevhid",
        content="Tevhid, Allah'ın birligine inanmaktir.\n\nDallari: rububiyet, uluhiyet.",
    )
    assert result.success, result.error
    path = Path(result.output["path"])
    assert path.is_file()
    assert path.stat().st_size > 1000
    assert any(e["name"] == "tevhid.docx" for e in result.output["verified_listing"])
