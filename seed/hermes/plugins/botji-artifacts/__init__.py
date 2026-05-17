"""Botji generic artifact fidelity plugin.

The plugin turns files into durable Botji artifacts before the agent reasons
about them. It records checksums, adapter evidence, lineage, Codex-backed image
transforms, and review receipts.

Package layout
──────────────
_constants.py   — shared constants (models, suffix sets, axis names)
_schemas.py     — tool JSON schema definitions
_utils.py       — path helpers, ID generation, file utilities
_detect.py      — MIME type and adapter detection
_registry.py    — artifact registry I/O (index, evidence, registration)
_extraction.py  — schema normalisation and per-adapter metadata extraction
_normalization.py — exact-copy and schema-render transform operations
_rendering.py   — output artifact creation, PNG preview, schema markdown
_codex.py       — Codex OAuth client, image generation, vision comparison
_vision.py      — vision review prompts, JSON parsing, conflict classification
_review.py      — fidelity review logic, comparators, and axes assembly
_handlers.py    — tool handler functions (thin dispatchers)
prompts/        — AI instruction templates as .md files
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Add this directory to sys.path so sibling submodules are importable.
_PLUGIN_DIR = str(Path(__file__).parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from _schemas import (  # noqa: E402
    ARTIFACT_REGISTER_SCHEMA,
    ARTIFACT_EXTRACT_SCHEMA,
    ARTIFACT_NORMALIZE_SCHEMA,
    ARTIFACT_TRANSFORM_SCHEMA,
    ARTIFACT_REVIEW_SCHEMA,
    ARTIFACT_LIST_SCHEMA,
    ARTIFACT_READ_SCHEMA,
)
from _handlers import (  # noqa: E402
    _handle_artifact_register,
    _handle_artifact_extract,
    _handle_artifact_normalize,
    _handle_artifact_transform,
    _handle_artifact_review,
    _handle_artifact_list,
    _handle_artifact_read,
    _write_verdict_file,
)
from _truncate import transform as _truncate_transform  # noqa: E402

logger = logging.getLogger(__name__)


def _on_tool_result(
    tool_name: str,
    args: dict,
    result: Any,
    *,
    session_id: str = "",
    **_: Any,
) -> str | None:
    """transform_tool_result hook: verdict-write for reviews, truncate large outputs.

    Two responsibilities multiplexed by tool_name:

    1. ``artifact_review`` → side-effect: write the verdict file that botji-gate
       reads. Return None so the agent sees the full review payload unchanged.

    2. Any other tool → delegate to _truncate.transform which decides whether to
       rewrite a large result. vision_analyze (162K chars) and skill_view (15K
       chars × N skills) are the main offenders — they get capped/dedup'd so
       compression doesn't fire every 2-3 turns.

    Plugin tool handlers do not receive session_id (registry.dispatch only
    forwards task_id + user_task). transform_tool_result IS called with
    session_id by model_tools.handle_function_call — this is where we get it.
    """
    if tool_name == "artifact_review" and session_id:
        try:
            payload = json.loads(result) if isinstance(result, str) else (result or {})
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(payload, dict) and payload.get("success"):
            review = payload.get("review")
            if isinstance(review, dict):
                try:
                    _write_verdict_file(
                        session_id=session_id,
                        verdict=str(payload.get("verdict") or ""),
                        delivery_gate=str(payload.get("delivery_gate") or ""),
                        recommended_action=str(payload.get("recommended_action") or ""),
                        primary_blocker=payload.get("primary_blocker"),
                        retry_guidance=payload.get("retry_guidance"),
                        review=review,
                    )
                    logger.info(
                        "botji-artifacts: wrote verdict file session=%s gate=%s",
                        session_id, payload.get("delivery_gate"),
                    )
                except Exception:
                    logger.exception("botji-artifacts: failed to write verdict file")
        return None  # don't truncate review output — agent needs full detail to ship

    # Everything else: delegate truncation policy.
    try:
        return _truncate_transform(tool_name, args or {}, result, session_id)
    except Exception:
        logger.exception("botji-artifacts: truncation hook failed (tool=%s)", tool_name)
        return None


def register(ctx) -> None:
    ctx.register_tool(
        name="artifact_register",
        toolset="file",
        schema=ARTIFACT_REGISTER_SCHEMA,
        handler=_handle_artifact_register,
        description=ARTIFACT_REGISTER_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_extract",
        toolset="file",
        schema=ARTIFACT_EXTRACT_SCHEMA,
        handler=_handle_artifact_extract,
        description=ARTIFACT_EXTRACT_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_normalize",
        toolset="file",
        schema=ARTIFACT_NORMALIZE_SCHEMA,
        handler=_handle_artifact_normalize,
        description=ARTIFACT_NORMALIZE_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_transform",
        toolset="file",
        schema=ARTIFACT_TRANSFORM_SCHEMA,
        handler=_handle_artifact_transform,
        description=ARTIFACT_TRANSFORM_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_review",
        toolset="file",
        schema=ARTIFACT_REVIEW_SCHEMA,
        handler=_handle_artifact_review,
        description=ARTIFACT_REVIEW_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_list",
        toolset="file",
        schema=ARTIFACT_LIST_SCHEMA,
        handler=_handle_artifact_list,
        description=ARTIFACT_LIST_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_read",
        toolset="file",
        schema=ARTIFACT_READ_SCHEMA,
        handler=_handle_artifact_read,
        description=ARTIFACT_READ_SCHEMA["description"],
    )
    ctx.register_hook("transform_tool_result", _on_tool_result)
