"""V1R render operations — named-function dispatch instead of switch statements.

Each operation is a callable with the same signature::

    operation(sources: list[Path], policy: RenderPolicy, **kwargs) -> RenderResult

The three operations:

- ``exact_copy`` — copy source bytes through unchanged (preserves SHA).
- ``render_schema`` — generate a typed schema from sources (no image).
- ``edit_image`` — provider-routed image transform (Codex / others).

Replaces the switch-statement dispatch in legacy transform consumers. Provider
calls live in ``providers/openai_codex.py``; ``operations.py`` is the
named-function surface botji-render-router consumes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# C2PA stamping is on by default (EU AI Act Article 50 compliance, Aug 2 2026).
# Set BOTJI_C2PA_ENABLED=0/false to disable for a single render call.
_C2PA_ENV = "BOTJI_C2PA_ENABLED"


def _c2pa_enabled() -> bool:
    raw = os.environ.get(_C2PA_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


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


def _load_openai_codex_provider() -> Any:
    """Load the bundled provider without using the global ``providers`` name.

    The upstream Hermes runtime already has an ``/opt/hermes/providers`` package.
    If that package is imported first, ``from providers.openai_codex`` resolves
    against the upstream package and misses Botji's bundled provider. Loading by
    file path keeps this plugin independent of global import order.
    """
    module_name = "botji_render_openai_codex"
    provider_path = Path(__file__).resolve().parent / "providers" / "openai_codex.py"
    loaded = sys.modules.get(module_name)
    if loaded is not None and Path(getattr(loaded, "__file__", "")) == provider_path:
        return loaded
    spec = importlib.util.spec_from_file_location(module_name, provider_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load openai_codex provider from {provider_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def edit_image(
    sources: list[Path],
    policy: RenderPolicy,
    *,
    output_dir: Path,
    **kwargs: Any,
) -> RenderResult:
    """Provider-routed image transform."""
    try:
        generate_image = _load_openai_codex_provider().generate_image
    except Exception as exc:
        return RenderResult(
            operation="edit_image", output_path=None,
            error=f"openai_codex provider not loadable: {exc}",
            error_type=type(exc).__name__,
        )
    return generate_image(sources, policy, output_dir=output_dir, **kwargs)


OPERATIONS: dict[str, Callable[..., RenderResult]] = {
    "exact_copy": exact_copy,
    "render_schema": render_schema,
    "edit_image": edit_image,
}


def _maybe_stamp_c2pa(
    result: RenderResult,
    sources: list[Path],
    *,
    botji_version: str,
    user_prompt_redacted: str,
    ai_model: str,
) -> RenderResult:
    """Stamp the result's PNG with a C2PA manifest, if enabled.

    Failure to stamp MUST NOT fail the render — log a warning, mark
    ``metadata['c2pa_stamped'] = False`` on the result, and return the
    unstamped PNG. Only PNG outputs are stamped (schemas / JSON pass
    through unchanged).
    """
    if not result.success or result.output_path is None:
        return result
    if result.output_path.suffix.lower() != ".png":
        result.metadata.setdefault("c2pa_stamped", False)
        result.metadata.setdefault("c2pa_skip_reason", "non-png output")
        return result
    if not _c2pa_enabled():
        result.metadata["c2pa_stamped"] = False
        result.metadata["c2pa_skip_reason"] = f"{_C2PA_ENV} disabled"
        return result
    try:
        from c2pa_stamp import stamp_png  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 — soft failure
        logger.warning("botji-render: c2pa_stamp import failed: %s", exc)
        result.metadata["c2pa_stamped"] = False
        result.metadata["c2pa_skip_reason"] = f"import-failed:{type(exc).__name__}"
        return result

    # Stamp in place: write to a sibling .stamped.png then atomically replace.
    raw = result.output_path
    staged = raw.with_suffix(raw.suffix + ".stamped")
    try:
        summary = stamp_png(
            raw,
            staged,
            botji_version=botji_version,
            source_image_paths=list(sources),
            user_prompt_redacted=user_prompt_redacted,
            ai_model=ai_model,
        )
        # Atomic-ish replace: Path.replace overwrites destination atomically
        # on the same volume (POSIX rename / Windows MoveFileEx semantics).
        staged.replace(raw)
        result.metadata["c2pa_stamped"] = True
        result.metadata["c2pa"] = summary
    except Exception as exc:  # noqa: BLE001 — soft failure
        logger.warning("botji-render: c2pa stamping failed: %s", exc)
        result.metadata["c2pa_stamped"] = False
        result.metadata["c2pa_error"] = f"{type(exc).__name__}: {exc}"[:300]
        # Best-effort cleanup of the staged file.
        try:
            if staged.exists():
                staged.unlink()
        except OSError:
            pass
    return result


def dispatch(operation: str, sources: list[Path], policy: RenderPolicy,
             *, output_dir: Path, **kwargs: Any) -> RenderResult:
    """Look up the named operation and invoke it. Returns error result for unknown ops.

    Post-processing: if the operation produced a PNG, the dispatcher stamps
    it with a C2PA manifest (EU AI Act Article 50) before returning. Stamp
    failure is logged + recorded in ``metadata['c2pa_stamped']=False`` but
    never fails the render — the unstamped PNG is still returned.
    """
    fn = OPERATIONS.get(operation)
    if fn is None:
        return RenderResult(
            operation=operation, output_path=None,
            error=f"unknown operation: {operation!r}. Known: {sorted(OPERATIONS.keys())}",
            error_type="ValueError",
        )
    try:
        result = fn(sources, policy, output_dir=output_dir, **kwargs)
    except Exception as exc:  # noqa: BLE001 — render must fail soft, not crash
        logger.warning("botji-render: %s raised: %s", operation, exc)
        return RenderResult(
            operation=operation, output_path=None,
            error=str(exc)[:500], error_type=type(exc).__name__,
        )
    botji_version = str(
        kwargs.get("botji_version") or policy.extras.get("botji_version") or "unknown"
    )
    user_prompt_redacted = str(
        kwargs.get("user_prompt_redacted")
        or policy.extras.get("user_prompt_redacted")
        or ""
    )
    ai_model = str(
        kwargs.get("ai_model") or policy.extras.get("ai_model") or "gpt-image-2"
    )
    return _maybe_stamp_c2pa(
        result, sources,
        botji_version=botji_version,
        user_prompt_redacted=user_prompt_redacted,
        ai_model=ai_model,
    )
