"""Adapter protocol — V1R boundary between review/delivery and file-type details.

Four operations every file-type adapter implements:

- ``detect(path)``    — does this adapter handle the given file?
- ``extract(path)``   — read the file, return adapter-specific evidence
- ``normalize(ev)``   — coerce evidence to a canonical schema shape
- ``compare(s, o, p)`` — compare source evidence against output under policy

The typed dataclasses below are the contract: ``Evidence`` flows up from
adapters into review; ``CompareResult`` flows back as verdict; ``ReviewPolicy``
carries hints from the calling skill (which route, strict mode).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class Evidence:
    """Adapter-extracted evidence about an artifact.

    Every adapter MUST set ``adapter``, ``mime_type``, ``sha256``,
    ``size_bytes``. The ``metadata`` dict is adapter-specific and the schema
    of its keys is documented per adapter (e.g. image: ``width``, ``height``,
    ``mode``; pdf: ``page_count``, ``page_sizes``).
    """
    adapter: str
    mime_type: str
    sha256: str
    size_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompareResult:
    """Verdict of comparing source evidence against output evidence."""
    verdict: str               # "pass" | "warn" | "block"
    reasons: list[str] = field(default_factory=list)   # machine-readable codes
    fields: dict[str, Any] = field(default_factory=dict)  # detail for receipts


@dataclass
class ReviewPolicy:
    """Hints from the calling skill about how strict to be."""
    route: str = "render_brief"   # "exact_copy" | "edit_image" | "render_schema" | "render_brief"
    strict: bool = False
    extras: dict[str, Any] = field(default_factory=dict)  # adapter-specific knobs


@runtime_checkable
class Adapter(Protocol):
    """Per-file-type interface. Every adapter (image, pdf, text, ...) implements this.

    Implementations live in sibling files under this plugin
    (``image.py``, ``pdf.py``, ``text.py``, ...). The plugin's REGISTRY in
    ``__init__.py`` maps names to instances; ``detect_adapter(path)`` tries
    every registered adapter until one claims the file.
    """

    name: str

    def detect(self, path: Path) -> bool: ...

    def extract(self, path: Path) -> Evidence: ...

    def normalize(self, evidence: Evidence) -> dict[str, Any]: ...

    def compare(
        self,
        source: Evidence,
        output: Evidence,
        policy: ReviewPolicy,
    ) -> CompareResult: ...
