"""V1R Codex provider — image generation via the existing Hermes-wired Codex client.

V1R PR 9a (this PR) ships a minimal stub that delegates to the existing
``botji-artifacts/_codex.py`` implementation. PR 9b moves that code here
and deletes the orphan.

The stub records the intent (provider, route, source count) and returns a
RenderResult — a workflow can wire its own consumer once the implementation
lands. This keeps the operations.py edit_image path callable without
crashing in the meantime.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def generate_image(
    sources: list[Path],
    policy: Any,
    *,
    output_dir: Path,
    **kwargs: Any,
) -> Any:
    """Stub. Real implementation migrates from botji-artifacts/_codex.py in PR 9b."""
    # Lazy import to avoid plugin-load coupling.
    from operations import RenderResult  # type: ignore[import-not-found]
    logger.info(
        "botji-render.openai_codex: generate_image called (sources=%d, route=%s) — "
        "PR 9a stub; real implementation lands in PR 9b",
        len(sources),
        getattr(policy, "route", None),
    )
    return RenderResult(
        operation="edit_image",
        output_path=None,
        error="openai_codex.generate_image not yet implemented (PR 9b)",
        error_type="NotImplementedError",
        metadata={
            "provider": "openai-codex",
            "route": "artifact_transform.edit_image.openai_codex",
            "source_count": len(sources),
            "policy_route": getattr(policy, "route", None),
        },
    )
