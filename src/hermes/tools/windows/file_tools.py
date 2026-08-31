from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.windows.input_backend import run_in_thread


def resolve_user_path(raw: str) -> Path:
    """Relative paths anchor to Desktop (e.g. Hermes2/tevhid.docx)."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("path gerekli")
    path = Path(text).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (Path.home() / "Desktop" / path).resolve()


def _dir_listing(folder: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not folder.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name.lower())[:limit]:
        entries.append(
            {
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            }
        )
    return entries


class ListDirectoryTool(BaseTool):
    name = "list_directory"
    description = "Klasor icerigini listeler (dogrulama icin)."
    risk_level = RiskLevel.READ_ONLY
    category = "files"

    async def execute(self, path: str = "", **kwargs: Any) -> ToolExecutionResult:
        raw = (path or kwargs.get("dir") or ".").strip()
        try:
            target = resolve_user_path(raw) if raw not in (".", "") else Path.home() / "Desktop"
            if not target.exists():
                return ToolExecutionResult(success=False, error=f"Yol yok: {target}")
            if target.is_file():
                stat = target.stat()
                return ToolExecutionResult(
                    success=True,
                    output={
                        "path": str(target),
                        "type": "file",
                        "size": stat.st_size,
                        "exists": True,
                    },
                    verified=True,
                )
            entries = _dir_listing(target)
            return ToolExecutionResult(
                success=True,
                output={"path": str(target), "type": "dir", "entries": entries, "count": len(entries)},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Klasor yolu veya Desktop alti goreli yol"},
            },
        }


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Metin dosyasi olusturur veya uzerine yazar (.txt, .md, .json vb.)."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "files"

    async def execute(
        self,
        path: str = "",
        content: str = "",
        append: bool = False,
        encoding: str = "utf-8",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        raw_path = (path or kwargs.get("file") or "").strip()
        text = content if content else str(kwargs.get("text") or "")
        if not raw_path:
            return ToolExecutionResult(success=False, error="path gerekli")
        try:
            target = resolve_user_path(raw_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with target.open("a", encoding=encoding, newline="\n") as handle:
                    handle.write(text)
            else:
                target.write_text(text, encoding=encoding, newline="\n")
            stat = target.stat()
            return ToolExecutionResult(
                success=True,
                output={
                    "path": str(target),
                    "size": stat.st_size,
                    "exists": target.is_file(),
                    "verified_listing": _dir_listing(target.parent, limit=20),
                },
                verified=target.is_file() and stat.st_size >= 0,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Dosya yolu (or. Hermes2/notlar.txt)"},
                "content": {"type": "string", "description": "Dosya icerigi"},
                "append": {"type": "boolean", "default": False},
            },
            "required": ["path", "content"],
        }


class CreateWordDocumentTool(BaseTool):
    name = "create_word_document"
    description = "Word (.docx) dosyasi olusturur ve paragraf/metin ekler."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "files"

    async def execute(
        self,
        path: str = "",
        content: str = "",
        title: str = "",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        raw_path = (path or kwargs.get("file") or "").strip()
        if not raw_path.lower().endswith(".docx"):
            raw_path = f"{raw_path}.docx" if raw_path else "document.docx"
        body = content or str(kwargs.get("text") or "")
        heading = (title or kwargs.get("heading") or "").strip()

        def _build() -> dict[str, Any]:
            from docx import Document

            target = resolve_user_path(raw_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            doc = Document()
            if heading:
                doc.add_heading(heading, level=0)
            paragraphs = [part.strip() for part in body.replace("\r\n", "\n").split("\n\n") if part.strip()]
            if not paragraphs and body.strip():
                paragraphs = [line.strip() for line in body.split("\n") if line.strip()]
            for para in paragraphs:
                doc.add_paragraph(para)
            if not paragraphs and not heading:
                doc.add_paragraph("")
            doc.save(str(target))
            stat = target.stat()
            return {
                "path": str(target),
                "size": stat.st_size,
                "exists": target.is_file(),
                "paragraph_count": len(paragraphs),
                "verified_listing": _dir_listing(target.parent, limit=20),
            }

        try:
            data = await run_in_thread(_build)
            return ToolExecutionResult(success=True, output=data, verified=bool(data.get("exists")))
        except ImportError:
            return ToolExecutionResult(
                success=False,
                error="python-docx kurulu degil. pip install python-docx veya client yeniden build edin.",
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Docx yolu (or. Hermes2/tevhid.docx)"},
                "title": {"type": "string", "description": "Baslik (opsiyonel)"},
                "content": {"type": "string", "description": "Govde metni; paragraflar icin bos satir birak"},
            },
            "required": ["path", "content"],
        }


class DeletePathTool(BaseTool):
    name = "delete_path"
    description = "Dosya veya klasoru siler (klasor icin recursive=true)."
    risk_level = RiskLevel.HIGH_RISK
    category = "files"

    async def execute(
        self,
        path: str = "",
        recursive: bool = False,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        raw = (path or kwargs.get("target") or "").strip()
        if not raw:
            return ToolExecutionResult(success=False, error="path gerekli")
        try:
            target = resolve_user_path(raw)
            parent = target.parent
            name = target.name
            if not target.exists():
                return ToolExecutionResult(success=False, error=f"Yol bulunamadi: {target}")
            if target.is_dir():
                if recursive:
                    shutil.rmtree(target)
                else:
                    target.rmdir()
            else:
                target.unlink()
            still_exists = target.exists()
            return ToolExecutionResult(
                success=not still_exists,
                output={
                    "deleted": name,
                    "path": str(target),
                    "exists_after": still_exists,
                    "verified_listing": _dir_listing(parent, limit=20) if parent.is_dir() else [],
                },
                verified=not still_exists,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Silinecek dosya/klasor yolu"},
                "recursive": {
                    "type": "boolean",
                    "default": False,
                    "description": "Dolu klasor silmek icin true",
                },
            },
            "required": ["path"],
        }
