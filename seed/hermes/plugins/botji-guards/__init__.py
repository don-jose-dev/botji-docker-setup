"""Botji cross-cutting guards.

Extracted from ``botji-core/hooks/`` in V1R PR 11 so the substrate stays
pure state + tools. Owns the two enforcement hooks; both fail open so a
guard error never breaks a tool call or a final reply.

Hooks:
  - ``pre_tool_call``  — mixed-family + over-budget transform enforcement.
  - ``transform_llm_output`` — last-line rewrite on source-bound output
    delivered without a passing receipt.

Read path into the substrate goes through ``_records.read_records``, which
opens the same JSONL files botji-core writes (no Python-level coupling).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    if not hasattr(ctx, "register_hook"):
        logger.warning("botji-guards: ctx has no register_hook — hooks disabled")
        return
    from .delivery_check import transform_llm_output
    from .stale_id_block import pre_tool_call
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("transform_llm_output", transform_llm_output)
    logger.info("botji-guards: pre_tool_call + transform_llm_output registered")
