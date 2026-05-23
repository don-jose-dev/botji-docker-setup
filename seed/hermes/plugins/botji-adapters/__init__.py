"""V1R adapter plugin — per-file-type detect/extract/normalize/compare protocol.

The substrate boundary V1R PR 2 introduces: above this boundary, review and
delivery code consumes typed ``Evidence`` and ``CompareResult``; below it,
each file-type adapter (image, pdf, text, dxf, docx, xlsx) owns its format
details. Replaces the scattered per-format branches in ``botji-artifacts``
(``_extraction.py``, ``_comparators.py``, ``_normalization.py``).

Per the V1R contract, this PR introduces the protocol and the **image**
adapter. Subsequent V1R PRs (3–7) add pdf, text, dxf, docx, xlsx adapters,
each migrating its format-specific code out of ``botji-artifacts`` in the
same PR that adds the adapter.

The plugin's ``register()`` is a no-op: adapters are accessed by direct
import (``from botji_adapters.image import ImageAdapter``) rather than by
tool dispatch. The plugin entry exists only so the loader does its standard
sanity checks (presence of ``plugin.yaml`` + ``__init__.py``).
"""
from __future__ import annotations

import logging

from protocol import Adapter, Evidence, CompareResult, ReviewPolicy  # noqa: I001
from image import ImageAdapter  # noqa: I001
from pdf import PdfAdapter  # noqa: I001
from text import TextAdapter  # noqa: I001
from dxf import DxfAdapter  # noqa: I001
from docx import DocxAdapter  # noqa: I001
from xlsx import XlsxAdapter  # noqa: I001

logger = logging.getLogger(__name__)

# Registry of file-type adapters. Each subsequent V1R PR adds an entry here
# alongside the adapter file. botji-review consumes this registry to dispatch
# extract/compare calls to the right adapter without per-file-type branches.
REGISTRY: dict[str, Adapter] = {
    "image": ImageAdapter(),
    "pdf": PdfAdapter(),
    "text": TextAdapter(),
    "dxf": DxfAdapter(),
    "docx": DocxAdapter(),
    "xlsx": XlsxAdapter(),
}


def get_adapter(name: str) -> Adapter | None:
    """Look up an adapter by name. Returns None for unknown file types."""
    return REGISTRY.get(name)


def detect_adapter(path: "Any") -> Adapter | None:
    """Try every registered adapter's detect() until one claims the file."""
    for adapter in REGISTRY.values():
        try:
            if adapter.detect(path):
                return adapter
        except Exception as exc:  # noqa: BLE001 — detection must not crash
            logger.warning("botji-adapters: %s.detect() raised: %s", adapter.name, exc)
            continue
    return None


def register(ctx) -> None:  # noqa: ARG001 — no-op; adapters are import-only
    """Plugin entry point. No tools or hooks; adapters used via direct import."""
    logger.info(
        "botji-adapters: registered (%d adapter%s: %s)",
        len(REGISTRY),
        "" if len(REGISTRY) == 1 else "s",
        ", ".join(sorted(REGISTRY.keys())),
    )


__all__ = ["Adapter", "Evidence", "CompareResult", "ReviewPolicy",
           "ImageAdapter", "PdfAdapter", "TextAdapter", "REGISTRY",
           "get_adapter", "detect_adapter"]
