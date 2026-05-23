"""V1R declarative review engine plugin.

Loads ``axes.yaml`` and evaluates applicable axes per call via
``engine.review()``. The engine is consumed by ``botji-artifact-fidelity``
(in a follow-up PR) and by any future skill that needs to produce a typed
review verdict from typed Evidence + a ReviewPolicy.

The plugin's ``register()`` is a no-op; the engine is accessed by direct
import (``from engine import review, list_axes``) like the other V1R
plugins (botji-adapters).
"""
from __future__ import annotations

import logging

from engine import (  # noqa: I001
    AxisResult,
    ReviewResult,
    list_axes,
    review,
)

logger = logging.getLogger(__name__)


def register(ctx) -> None:  # noqa: ARG001 — no-op
    """Plugin entry point. No tools or hooks."""
    axes = list_axes()
    logger.info(
        "botji-review: registered (%d axis%s: %s)",
        len(axes),
        "" if len(axes) == 1 else "es",
        ", ".join(sorted(axes)),
    )


__all__ = ["AxisResult", "ReviewResult", "list_axes", "review"]
