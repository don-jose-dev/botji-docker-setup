"""Image adapter — implements the V1R Adapter protocol for image/* file types.

Handles PNG, JPEG, WebP, GIF, TIFF, BMP. Extraction reads pixel dimensions,
color mode, and the content SHA. Comparison checks byte-exact equality for
``exact_copy`` route, dimensions + mode for other routes (visual-fidelity
review is a separate skill-level concern; this adapter only does mechanical
checks the substrate is allowed to do per the charter).

V1R PR 2 ships the adapter wired through the registry. Per-tool consumer
switches in ``botji-artifacts`` come in this same PR (image comparator
dispatch). Subsequent V1R PRs migrate the remaining image-specific helper
functions from ``botji-artifacts`` into this file and delete the orphans.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Any

from protocol import CompareResult, Evidence, ReviewPolicy  # noqa: I001 — plugin-dir on sys.path, see plugin loader

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tiff", ".tif", ".bmp"}
_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/webp", "image/gif",
    "image/tiff", "image/bmp",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_dimensions(path: Path) -> tuple[int | None, int | None, str | None]:
    """Best-effort image dimensions + mode via Pillow if available.

    Fail-soft: missing Pillow returns (None, None, None); a corrupt image
    returns (None, None, None) with a logged warning. The substrate never
    crashes on malformed user uploads — that's a delivery-gate concern.
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return None, None, None
    try:
        with Image.open(path) as img:
            return img.size[0], img.size[1], img.mode
    except Exception as exc:  # noqa: BLE001 — substrate must not crash
        logger.warning("ImageAdapter: failed to read %s: %s", path, exc)
        return None, None, None


class ImageAdapter:
    """Image adapter. See module docstring for scope."""

    name = "image"

    def detect(self, path: Path) -> bool:
        if path.suffix.lower() in _IMAGE_SUFFIXES:
            return True
        mime, _ = mimetypes.guess_type(str(path))
        return mime in _IMAGE_MIMES

    def extract(self, path: Path) -> Evidence:
        mime, _ = mimetypes.guess_type(str(path))
        width, height, mode = _read_dimensions(path)
        metadata: dict[str, Any] = {}
        if width is not None:
            metadata["width"] = width
        if height is not None:
            metadata["height"] = height
        if mode is not None:
            metadata["mode"] = mode
        return Evidence(
            adapter=self.name,
            mime_type=mime or "image/unknown",
            sha256=_sha256(path),
            size_bytes=path.stat().st_size,
            metadata=metadata,
        )

    def normalize(self, evidence: Evidence) -> dict[str, Any]:
        """Canonical schema shape for image evidence."""
        return {
            "adapter": evidence.adapter,
            "mime_type": evidence.mime_type,
            "sha256": evidence.sha256,
            "size_bytes": evidence.size_bytes,
            "width": evidence.metadata.get("width"),
            "height": evidence.metadata.get("height"),
            "mode": evidence.metadata.get("mode"),
        }

    def compare(
        self,
        source: Evidence,
        output: Evidence,
        policy: ReviewPolicy,
    ) -> CompareResult:
        """Mechanical image comparison. Vision review is a skill concern, not this."""
        reasons: list[str] = []
        fields: dict[str, Any] = {
            "source_sha": source.sha256,
            "output_sha": output.sha256,
            "source_dims": (source.metadata.get("width"), source.metadata.get("height")),
            "output_dims": (output.metadata.get("width"), output.metadata.get("height")),
        }

        # exact_copy route: bytes must match
        if policy.route == "exact_copy":
            if source.sha256 != output.sha256:
                reasons.append("byte_exact_fidelity_failed")
                return CompareResult(verdict="block", reasons=reasons, fields=fields)
            return CompareResult(verdict="pass", reasons=["byte_exact_match"], fields=fields)

        # Other routes: dimensions and mode should match unless explicitly resized
        if source.metadata.get("width") and output.metadata.get("width"):
            if source.metadata["width"] != output.metadata["width"]:
                reasons.append("width_mismatch")
            if source.metadata.get("height") != output.metadata.get("height"):
                reasons.append("height_mismatch")

        if reasons:
            verdict = "block" if policy.strict else "warn"
            return CompareResult(verdict=verdict, reasons=reasons, fields=fields)

        return CompareResult(verdict="pass", reasons=[], fields=fields)
