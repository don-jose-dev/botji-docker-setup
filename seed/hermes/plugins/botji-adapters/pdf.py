"""PDF adapter — implements the V1R Adapter protocol for application/pdf.

Extraction uses raw PDF parsing for page count (counts ``/Type /Page`` markers
in the document body). No external dependency on pdfplumber / PyPDF2 — keeps
the substrate light and the container image small. If a workflow needs deeper
PDF analysis (text extraction, OCR), that's a skill-level concern, not the
substrate's.

Comparison checks: byte-exact on ``exact_copy`` route; page-count + size for
other routes (warn vs block under strict).

V1R PR 3 ships the adapter through the registry. Per-tool consumer migration
in ``botji-artifacts`` follows in the PRs that consume the protocol
(PR 8 declarative review axes, PR 9 render plugin consolidation, PR 11 drops
the legacy plugin entirely).
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from protocol import CompareResult, Evidence, ReviewPolicy  # noqa: I001

logger = logging.getLogger(__name__)

_PDF_SUFFIXES = {".pdf"}
_PDF_HEADER = b"%PDF"

# Match "/Type /Page" and "/Type/Page" with optional whitespace. Avoids
# counting "/Type /Pages" (the catalog) via the negative-lookbehind on 's'.
_PAGE_MARKER = re.compile(rb"/Type\s*/Page(?!s)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_pages(path: Path) -> int | None:
    """Best-effort page count via raw byte scan. None on read failure.

    Counts the number of ``/Type /Page`` markers (excluding ``/Type /Pages``,
    which is the catalog object, not a page). Fast: no PDF library, no text
    extraction, just a regex over the raw bytes.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning("PdfAdapter: failed to read %s: %s", path, exc)
        return None
    return len(_PAGE_MARKER.findall(data))


class PdfAdapter:
    """PDF adapter. See module docstring for scope."""

    name = "pdf"

    def detect(self, path: Path) -> bool:
        if path.suffix.lower() in _PDF_SUFFIXES:
            return True
        try:
            with path.open("rb") as handle:
                head = handle.read(8)
        except OSError:
            return False
        return head.startswith(_PDF_HEADER)

    def extract(self, path: Path) -> Evidence:
        metadata: dict[str, Any] = {}
        page_count = _count_pages(path)
        if page_count is not None:
            metadata["page_count"] = page_count
        return Evidence(
            adapter=self.name,
            mime_type="application/pdf",
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
            "page_count": evidence.metadata.get("page_count"),
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
            "source_pages": source.metadata.get("page_count"),
            "output_pages": output.metadata.get("page_count"),
        }

        if policy.route == "exact_copy":
            if source.sha256 != output.sha256:
                reasons.append("byte_exact_fidelity_failed")
                return CompareResult(verdict="block", reasons=reasons, fields=fields)
            return CompareResult(verdict="pass", reasons=["byte_exact_match"], fields=fields)

        src_pages = source.metadata.get("page_count")
        out_pages = output.metadata.get("page_count")
        if src_pages is not None and out_pages is not None and src_pages != out_pages:
            reasons.append("page_count_mismatch")

        if reasons:
            verdict = "block" if policy.strict else "warn"
            return CompareResult(verdict=verdict, reasons=reasons, fields=fields)

        return CompareResult(verdict="pass", reasons=[], fields=fields)
