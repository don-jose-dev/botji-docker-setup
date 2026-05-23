"""DXF adapter — implements the V1R Adapter protocol for AutoCAD DXF files.

DXF is a structured text format with SECTION...ENDSEC blocks and ENTITIES.
Extraction counts entities (LINE/CIRCLE/ARC/etc.) without parsing the full
geometry — enough to detect "the user re-uploaded a different drawing" at
the substrate layer. Skills that need actual CAD geometry use ezdxf or
similar (out of scope for the substrate).

Compare semantics:
- exact_copy: byte-exact SHA match
- other routes: entity_count match within ±5%
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from protocol import CompareResult, Evidence, ReviewPolicy  # noqa: I001

logger = logging.getLogger(__name__)

_DXF_SUFFIXES = {".dxf"}
_ENTITY_NAMES = ("LINE", "CIRCLE", "ARC", "POLYLINE", "LWPOLYLINE",
                 "TEXT", "MTEXT", "INSERT", "DIMENSION", "ELLIPSE",
                 "SPLINE", "HATCH", "POINT", "SOLID", "3DFACE")
# Match `  0\nENTITYTYPE` (group code 0 introduces a new entity)
_ENTITY_RE = re.compile(
    r"\n\s*0\s*\n\s*(" + "|".join(_ENTITY_NAMES) + r")\s*\n",
    re.IGNORECASE,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_entities(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("DxfAdapter: failed to read %s: %s", path, exc)
        return None
    return len(_ENTITY_RE.findall(text))


def _looks_dxf(head: bytes) -> bool:
    return b"SECTION" in head[:2048] and b"ENTITIES" in head[:8192]


class DxfAdapter:
    """DXF (AutoCAD drawing exchange format) adapter."""

    name = "dxf"

    def detect(self, path: Path) -> bool:
        if path.suffix.lower() in _DXF_SUFFIXES:
            return True
        try:
            with path.open("rb") as handle:
                head = handle.read(8192)
        except OSError:
            return False
        return _looks_dxf(head)

    def extract(self, path: Path) -> Evidence:
        metadata: dict[str, Any] = {}
        ec = _count_entities(path)
        if ec is not None:
            metadata["entity_count"] = ec
        return Evidence(
            adapter=self.name,
            mime_type="image/vnd.dxf",
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
            "entity_count": evidence.metadata.get("entity_count"),
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
            "source_entities": source.metadata.get("entity_count"),
            "output_entities": output.metadata.get("entity_count"),
        }

        if policy.route == "exact_copy":
            if source.sha256 != output.sha256:
                reasons.append("byte_exact_fidelity_failed")
                return CompareResult(verdict="block", reasons=reasons, fields=fields)
            return CompareResult(verdict="pass", reasons=["byte_exact_match"], fields=fields)

        src_ec = source.metadata.get("entity_count")
        out_ec = output.metadata.get("entity_count")
        if src_ec and out_ec:
            tolerance = max(1, int(0.05 * src_ec))
            if abs(src_ec - out_ec) > tolerance:
                reasons.append("entity_count_drift")

        if reasons:
            verdict = "block" if policy.strict else "warn"
            return CompareResult(verdict=verdict, reasons=reasons, fields=fields)

        return CompareResult(verdict="pass", reasons=[], fields=fields)
