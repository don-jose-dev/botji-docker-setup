"""Text adapter — implements the V1R Adapter protocol for text/* file types.

Handles plain text, markdown, JSON, YAML, CSV, HTML, and any file whose
content decodes as UTF-8 without binary bytes. Extraction returns line count,
char count, and an optional language hint based on suffix. Comparison checks
byte-exact on ``exact_copy`` and line-count tolerance on other routes.

V1R PR 4 ships the adapter through the registry. As with the other adapters,
the orphan text-specific code in ``botji-artifacts`` is removed naturally as
PRs 8/9/11 land.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Any

from protocol import CompareResult, Evidence, ReviewPolicy  # noqa: I001

logger = logging.getLogger(__name__)

_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".json", ".yaml", ".yml",
    ".csv", ".tsv", ".log", ".html", ".htm", ".xml", ".ini", ".toml",
    ".py", ".js", ".ts", ".go", ".rs", ".java", ".sh", ".bash",
}
_LANG_HINTS = {
    ".md": "markdown", ".markdown": "markdown",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".csv": "csv", ".tsv": "tsv",
    ".html": "html", ".htm": "html", ".xml": "xml",
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java",
    ".sh": "shell", ".bash": "shell",
    ".toml": "toml", ".ini": "ini",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _looks_text(head: bytes) -> bool:
    """True if the first chunk decodes as UTF-8 without NUL bytes."""
    if not head:
        return True
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


class TextAdapter:
    """Text / structured-text adapter. See module docstring for scope."""

    name = "text"

    def detect(self, path: Path) -> bool:
        if path.suffix.lower() in _TEXT_SUFFIXES:
            return True
        # Content sniff: read up to 4KB, accept if it decodes as UTF-8 cleanly.
        try:
            with path.open("rb") as handle:
                head = handle.read(4096)
        except OSError:
            return False
        return _looks_text(head)

    def extract(self, path: Path) -> Evidence:
        mime, _ = mimetypes.guess_type(str(path))
        text = ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("TextAdapter: failed to read %s: %s", path, exc)
        metadata: dict[str, Any] = {
            "line_count": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
            "char_count": len(text),
        }
        lang = _LANG_HINTS.get(path.suffix.lower())
        if lang:
            metadata["language"] = lang
        return Evidence(
            adapter=self.name,
            mime_type=mime or "text/plain",
            sha256=_sha256(path),
            size_bytes=path.stat().st_size,
            metadata=metadata,
        )

    def normalize(self, evidence: Evidence) -> dict[str, Any]:
        return {
            "adapter": evidence.adapter,
            "mime_type": evidence.mime_type,
            "sha256": evidence.sha256,
            "size_bytes": evidence.size_bytes,
            "line_count": evidence.metadata.get("line_count"),
            "char_count": evidence.metadata.get("char_count"),
            "language": evidence.metadata.get("language"),
        }

    def compare(
        self,
        source: Evidence,
        output: Evidence,
        policy: ReviewPolicy,
    ) -> CompareResult:
        reasons: list[str] = []
        fields: dict[str, Any] = {
            "source_sha": source.sha256,
            "output_sha": output.sha256,
            "source_lines": source.metadata.get("line_count"),
            "output_lines": output.metadata.get("line_count"),
        }

        if policy.route == "exact_copy":
            if source.sha256 != output.sha256:
                reasons.append("byte_exact_fidelity_failed")
                return CompareResult(verdict="block", reasons=reasons, fields=fields)
            return CompareResult(verdict="pass", reasons=["byte_exact_match"], fields=fields)

        src_lines = source.metadata.get("line_count")
        out_lines = output.metadata.get("line_count")
        # 10 % tolerance on line count for non-exact routes; structured-text
        # transforms commonly add/remove a few lines for indentation.
        if src_lines and out_lines:
            tolerance = max(1, int(0.1 * src_lines))
            if abs(src_lines - out_lines) > tolerance:
                reasons.append("line_count_drift")

        if reasons:
            verdict = "block" if policy.strict else "warn"
            return CompareResult(verdict=verdict, reasons=reasons, fields=fields)

        return CompareResult(verdict="pass", reasons=[], fields=fields)
