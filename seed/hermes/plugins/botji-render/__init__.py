"""V1R render plugin.

Operations dispatcher (``exact_copy``, ``render_schema``, ``edit_image``)
and provider implementations. During cutover, legacy handlers consume this
module while artifact bookkeeping remains outside the provider.

Accessed by direct import:
    from operations import dispatch, OPERATIONS, RenderPolicy, RenderResult
"""
from __future__ import annotations

import logging

from operations import (  # noqa: I001
    OPERATIONS,
    RenderPolicy,
    RenderResult,
    dispatch,
    edit_image,
    exact_copy,
    render_schema,
)

logger = logging.getLogger(__name__)


def register(ctx) -> None:  # noqa: ARG001 — no-op
    logger.info(
        "botji-render: registered (%d operation%s: %s)",
        len(OPERATIONS),
        "" if len(OPERATIONS) == 1 else "s",
        ", ".join(sorted(OPERATIONS.keys())),
    )


__all__ = ["OPERATIONS", "RenderPolicy", "RenderResult", "dispatch",
           "edit_image", "exact_copy", "render_schema"]
