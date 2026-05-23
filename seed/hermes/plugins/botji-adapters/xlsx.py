"""XLSX adapter — implements the V1R Adapter protocol for .xlsx spreadsheets.

XLSX is a ZIP archive containing [Content_Types].xml + xl/ folder (workbook,
worksheets, sharedStrings). Extraction counts worksheets via xl/workbook.xml
<sheet> elements without parsing the full workbook. No openpyxl required.

Compare:
- exact_copy: byte-exact SHA
- other routes: sheet_count match (no tolerance — sheets are structural)
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

_XLSX_SUFFIXES = {".xlsx", ".xlsm"}
_SHEET_RE = re.compile(rb"<sheet\b")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_xlsx_workbook_xml(path: Path) -> bytes | None:
    try:
        with zipfile.ZipFile(path) as archive:
            if "xl/workbook.xml" not in archive.namelist():
                return None
            with archive.open("xl/workbook.xml") as fh:
                return fh.read()
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("XlsxAdapter: read failed %s: %s", path, exc)
        return None


def _count_sheets(path: Path) -> int | None:
    xml = _read_xlsx_workbook_xml(path)
    if xml is None:
        return None
    return len(_SHEET_RE.findall(xml))


def _looks_xlsx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names)


class XlsxAdapter:
    """XLSX (Office Open XML spreadsheet) adapter."""

    name = "xlsx"

    def detect(self, path: Path) -> bool:
        if path.suffix.lower() in _XLSX_SUFFIXES:
            return True
        try:
            with path.open("rb") as handle:
                head = handle.read(4)
        except OSError:
            return False
        if not head.startswith(b"PK"):
            return False
        return _looks_xlsx(path)

    def extract(self, path: Path) -> Evidence:
        metadata: dict[str, Any] = {}
        sc = _count_sheets(path)
        if sc is not None:
            metadata["sheet_count"] = sc
        return Evidence(
            adapter=self.name,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
            "sheet_count": evidence.metadata.get("sheet_count"),
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
            "source_sheets": source.metadata.get("sheet_count"),
            "output_sheets": output.metadata.get("sheet_count"),
        }

        if policy.route == "exact_copy":
            if source.sha256 != output.sha256:
                reasons.append("byte_exact_fidelity_failed")
                return CompareResult(verdict="block", reasons=reasons, fields=fields)
            return CompareResult(verdict="pass", reasons=["byte_exact_match"], fields=fields)

        src_sc = source.metadata.get("sheet_count")
        out_sc = output.metadata.get("sheet_count")
        # Sheet count is structural — no tolerance. Adding/removing a sheet
        # is a meaningful change to the document.
        if src_sc is not None and out_sc is not None and src_sc != out_sc:
            reasons.append("sheet_count_mismatch")

        if reasons:
            verdict = "block" if policy.strict else "warn"
            return CompareResult(verdict=verdict, reasons=reasons, fields=fields)

        return CompareResult(verdict="pass", reasons=[], fields=fields)
