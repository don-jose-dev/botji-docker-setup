"""V1R render operations — named-function dispatch instead of switch statements.

Each operation is a callable with the same signature::

    operation(sources: list[Path], policy: RenderPolicy, **kwargs) -> RenderResult

The three operations:

- ``exact_copy`` — copy source bytes through unchanged (preserves SHA).
- ``render_schema`` — generate a typed schema from sources (no image).
- ``edit_image`` — provider-routed image transform (Codex / others).

Replaces the switch-statement dispatch in
``botji-artifacts/_handlers.py::_handle_artifact_transform``. The actual
provider calls live in ``providers/openai_codex.py``. ``operations.py`` is
the named-function surface botji-render-router consumes.

V1R PR 9a (this PR) ships the operations module with explicit ``exact_copy``
and ``render_schema`` implementations. ``edit_image`` is delegated to the
existing Codex path in botji-artifacts via the provider module — PR 9b will
migrate that path here and delete the orphan code.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class RenderPolicy:
    """Per-call render policy. Hints from the calling skill."""
    route: str = "render_brief"   # "exact_copy" | "edit_image" | "render_schema" | "render_brief"
    target_size: str | None = None  # e.g., "1024x1024"
    strict: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class RenderResult:
    """Outcome of a render operation."""
    operation: str
    output_path: Path | None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_type: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None and self.output_path is not None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exact_copy(
    sources: list[Path],
    policy: RenderPolicy,
    *,
    output_dir: Path,
    **_: Any,
) -> RenderResult:
    """Byte-copy a single source to ``output_dir/<sha>.suffix``.

    Used when the workflow says "use this exact file as the output."
    The output SHA must equal the source SHA — that's the contract reviewed
    by the byte_exact_fidelity axis in the declarative review engine.
    """
    if len(sources) != 1:
        return RenderResult(
            operation="exact_copy", output_path=None,
            error=f"exact_copy needs exactly 1 source, got {len(sources)}",
            error_type="ValueError",
        )
    src = sources[0]
    if not src.is_file():
        return RenderResult(
            operation="exact_copy", output_path=None,
            error=f"source not found: {src}",
            error_type="FileNotFoundError",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    sha = _sha256(src)
    out = output_dir / f"{sha[:16]}{src.suffix}"
    if not out.exists():
        shutil.copy2(src, out)
    return RenderResult(
        operation="exact_copy", output_path=out,
        metadata={"sha256": sha, "source_path": str(src), "route": "exact_copy"},
    )


def render_schema(
    sources: list[Path],
    policy: RenderPolicy,
    *,
    output_dir: Path,
    schema_payload: dict[str, Any] | None = None,
    **_: Any,
) -> RenderResult:
    """Render a typed schema (no image generation, no provider call).

    Used for workflows that produce structured data — manifest, brief, plan —
    where the output is JSON/YAML, not pixels. The schema payload is supplied
    by the calling skill; this operation persists it and returns metadata.
    """
    if schema_payload is None:
        return RenderResult(
            operation="render_schema", output_path=None,
            error="render_schema requires schema_payload",
            error_type="ValueError",
        )
    import json
    output_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(schema_payload, indent=2, sort_keys=True, ensure_ascii=False)
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    out = output_dir / f"schema_{sha[:16]}.json"
    out.write_text(body, encoding="utf-8")
    return RenderResult(
        operation="render_schema", output_path=out,
        metadata={
            "sha256": sha,
            "size_bytes": len(body),
            "route": "render_schema",
            "source_count": len(sources),
        },
    )


def edit_image(
    sources: list[Path],
    policy: RenderPolicy,
    *,
    output_dir: Path,
    **kwargs: Any,
) -> RenderResult:
    """Provider-routed image transform.

    V1R PR 9a: delegates to ``providers/openai_codex.py``. PR 9b will move
    the existing ``botji-artifacts/_codex.py`` logic into the provider module
    and delete the orphan there.
    """
    try:
        from providers.openai_codex import generate_image  # type: ignore[import-not-found]
    except ImportError as exc:
        return RenderResult(
            operation="edit_image", output_path=None,
            error=f"openai_codex provider not loadable: {exc}",
            error_type="ImportError",
        )
    return generate_image(sources, policy, output_dir=output_dir, **kwargs)


OPERATIONS: dict[str, Callable[..., RenderResult]] = {
    "exact_copy": exact_copy,
    "render_schema": render_schema,
    "edit_image": edit_image,
}


def dispatch(operation: str, sources: list[Path], policy: RenderPolicy,
             *, output_dir: Path, **kwargs: Any) -> RenderResult:
    """Look up the named operation and invoke it. Returns error result for unknown ops."""
    fn = OPERATIONS.get(operation)
    if fn is None:
        return RenderResult(
            operation=operation, output_path=None,
            error=f"unknown operation: {operation!r}. Known: {sorted(OPERATIONS.keys())}",
            error_type="ValueError",
        )
    try:
        return fn(sources, policy, output_dir=output_dir, **kwargs)
    except Exception as exc:  # noqa: BLE001 — render must fail soft, not crash
        logger.warning("botji-render: %s raised: %s", operation, exc)
        return RenderResult(
            operation=operation, output_path=None,
            error=str(exc)[:500], error_type=type(exc).__name__,
        )
