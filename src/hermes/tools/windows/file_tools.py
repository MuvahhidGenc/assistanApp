from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult


def resolve_user_path(path: str) -> Path:
    """Resolve user-relative paths against Desktop by default."""
    text = path.strip().replace("\\", "/")
    home = Path.home()
    if text.lower().startswith("desktop/"):
        return home / "Desktop" / text.split("/", 1)[1]
    if "/" not in text and "\\" not in text:
        return home / "Desktop" / text
    if os.path.isabs(text):
        return Path(text)
    return home / "Desktop" / text


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Metin dosyasi yazar veya uzerine yazar."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "file"

    async def execute(self, path: str = "", content: str = "", **kwargs: Any) -> ToolExecutionResult:
        if not path:
            return ToolExecutionResult(success=False, error="path required")
        try:
            target = resolve_user_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return ToolExecutionResult(
                success=True,
                output={"path": str(target), "size": target.stat().st_size},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class ListDirectoryTool(BaseTool):
    name = "list_directory"
    description = "Klasor icerigini listeler."
    risk_level = RiskLevel.READ_ONLY
    category = "file"

    async def execute(self, path: str = ".", **kwargs: Any) -> ToolExecutionResult:
        try:
            target = resolve_user_path(path) if path not in (".", "") else Path(path)
            if not target.is_absolute():
                target = resolve_user_path(path)
            entries = []
            if target.is_dir():
                for item in sorted(target.iterdir()):
                    entries.append(
                        {
                            "name": item.name,
                            "is_dir": item.is_dir(),
                            "size": item.stat().st_size if item.is_file() else 0,
                        }
                    )
            return ToolExecutionResult(
                success=True,
                output={"path": str(target), "entries": entries},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class DeletePathTool(BaseTool):
    name = "delete_path"
    description = "Dosya veya klasoru siler."
    risk_level = RiskLevel.HIGH_RISK
    category = "file"

    async def execute(
        self,
        path: str = "",
        recursive: bool = False,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        if not path:
            return ToolExecutionResult(success=False, error="path required")
        try:
            target = resolve_user_path(path)
            existed = target.exists()
            if target.is_dir() and recursive:
                shutil.rmtree(target)
            elif target.is_dir():
                target.rmdir()
            elif target.is_file():
                target.unlink()
            return ToolExecutionResult(
                success=True,
                output={"path": str(target), "existed": existed, "exists_after": target.exists()},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class CreateFolderTool(BaseTool):
    name = "create_folder"
    description = "Yeni klasor olusturur."
    risk_level = RiskLevel.LOW_RISK
    category = "file"

    async def execute(self, path: str = "", **kwargs: Any) -> ToolExecutionResult:
        if not path:
            return ToolExecutionResult(success=False, error="path required")
        try:
            target = resolve_user_path(path)
            target.mkdir(parents=True, exist_ok=True)
            return ToolExecutionResult(
                success=True,
                output={"path": str(target), "created": True},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class CreateWordDocumentTool(BaseTool):
    name = "create_word_document"
    description = "Word (.docx) belgesi olusturur."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "file"

    async def execute(
        self,
        path: str = "",
        title: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        if not path:
            return ToolExecutionResult(success=False, error="path required")
        try:
            target = resolve_user_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_docx(target, title, content)

            listing_tool = ListDirectoryTool()
            listing = await listing_tool.execute(path=str(target.parent.name))
            verified = listing.output.get("entries", []) if listing.success else []

            return ToolExecutionResult(
                success=True,
                output={
                    "path": str(target),
                    "size": target.stat().st_size,
                    "verified_listing": verified,
                },
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    @staticmethod
    def _write_docx(target: Path, title: str, content: str) -> None:
        try:
            from docx import Document

            doc = Document()
            if title:
                doc.add_heading(title, level=1)
            for paragraph in content.split("\n\n"):
                if paragraph.strip():
                    doc.add_paragraph(paragraph.strip())
            doc.save(str(target))
            return
        except ImportError:
            pass

        import zipfile

        paragraphs = []
        if title:
            paragraphs.append(
                f'<w:p><w:r><w:t xml:space="preserve">{title}</w:t></w:r></w:p>'
            )
        for paragraph in content.split("\n\n"):
            if paragraph.strip():
                safe = paragraph.strip().replace("&", "&amp;").replace("<", "&lt;")
                paragraphs.append(
                    f'<w:p><w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'
                )
        body = "".join(paragraphs)
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}<w:sectPr/></w:body></w:document>"
        )
        content_types = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>"
        )
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", rels)
            archive.writestr("word/document.xml", document_xml)
