"""DOCX adapter — implements the V1R Adapter protocol for .docx files.

DOCX is a ZIP archive containing [Content_Types].xml + word/document.xml.
Extraction counts paragraphs from document.xml (looking for <w:p ...> opens)
without parsing the full XML — fast and dependency-light. No python-docx
required; a skill that needs deeper analysis can add it.

Compare:
- exact_copy: byte-exact SHA
- other routes: paragraph count match within ±10%
"""
from __future__ import annotations

import hashlib
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

from protocol import CompareResult, Evidence, ReviewPolicy  # noqa: I001

logger = logging.getLogger(__name__)

_DOCX_SUFFIXES = {".docx", ".docm"}
_PARA_RE = re.compile(rb"<w:p\b")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_docx_document_xml(path: Path) -> bytes | None:
    try:
        with zipfile.ZipFile(path) as archive:
            if "word/document.xml" not in archive.namelist():
                return None
            with archive.open("word/document.xml") as fh:
                return fh.read()
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("DocxAdapter: read failed %s: %s", path, exc)
        return None


def _count_paragraphs(path: Path) -> int | None:
    xml = _read_docx_document_xml(path)
    if xml is None:
        return None
    return len(_PARA_RE.findall(xml))


def _looks_docx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return "[Content_Types].xml" in names and any(n.startswith("word/") for n in names)


class DocxAdapter:
    """DOCX (Office Open XML word document) adapter."""

    name = "docx"

    def detect(self, path: Path) -> bool:
        if path.suffix.lower() in _DOCX_SUFFIXES:
            return True
        try:
            with path.open("rb") as handle:
                head = handle.read(4)
        except OSError:
            return False
        if not head.startswith(b"PK"):
            return False
        return _looks_docx(path)

    def extract(self, path: Path) -> Evidence:
        metadata: dict[str, Any] = {}
        pc = _count_paragraphs(path)
        if pc is not None:
            metadata["paragraph_count"] = pc
        return Evidence(
            adapter=self.name,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
            "paragraph_count": evidence.metadata.get("paragraph_count"),
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
            "source_paragraphs": source.metadata.get("paragraph_count"),
            "output_paragraphs": output.metadata.get("paragraph_count"),
        }

        if policy.route == "exact_copy":
            if source.sha256 != output.sha256:
                reasons.append("byte_exact_fidelity_failed")
                return CompareResult(verdict="block", reasons=reasons, fields=fields)
            return CompareResult(verdict="pass", reasons=["byte_exact_match"], fields=fields)

        src_pc = source.metadata.get("paragraph_count")
        out_pc = output.metadata.get("paragraph_count")
        if src_pc and out_pc:
            tolerance = max(1, int(0.1 * src_pc))
            if abs(src_pc - out_pc) > tolerance:
                reasons.append("paragraph_count_drift")

        if reasons:
            verdict = "block" if policy.strict else "warn"
            return CompareResult(verdict=verdict, reasons=reasons, fields=fields)

        return CompareResult(verdict="pass", reasons=[], fields=fields)
