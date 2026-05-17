"""Tool-output truncation + session-scoped dedupe for the transform_tool_result hook.

Hermes context compression breaks down when a single tool returns >100K chars: the
big payload sits inside ``protect_last_n`` and the compressor can't summarise it.
Two specific tools dominate our workload:

- ``vision_analyze`` returns ~162K chars (≈40K tokens, ~15% of the 272K context
  window). Once the model has formed a plan from this output, the full text is
  rarely re-read — it's just floor in every subsequent compression pass.

- ``skill_view`` returns 7-15K chars per skill. The agent re-reads 3-4 skills
  after every compression because the protected tail rolls forward; each re-read
  is another 30K+ chars of context for the same content it already saw.

This module enforces two policies via the ``transform_tool_result`` hook:

1. **Large-result truncation with on-disk full body.** vision_analyze results
   above ``_VISION_INLINE_MAX`` are written to ``/opt/data/vision_cache/<hash>.json``
   and replaced inline with a 1-line summary pointing at the cache file. The
   agent can re-read the file via ``read_file`` if it actually needs full detail.

2. **Session-scoped skill_view dedupe.** First read of a skill in a session
   returns the full content. Subsequent reads of the same skill name in the
   same session return a short "already loaded" placeholder.

Hooks fire AFTER the tool returns but BEFORE the result enters the model
conversation history. The truncated string is what gets remembered.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Truncation thresholds. Tuned for gpt-5.4-mini's 272K context window:
# the goal is to keep any single tool call well under 2K tokens so it can sit
# inside the protected tail without dominating the window.
_VISION_INLINE_MAX = 8_000        # ~2K tokens — keeps the planning bullets, drops the long inventory
_SKILL_INLINE_MAX = 4_000          # ~1K tokens — keeps the skill summary, agent reads file for syntax
_GENERIC_INLINE_MAX = 24_000       # ~6K tokens — broad safety net for any other large tool result

# Session-scoped state: which skill_view names have been served this session.
# Keyed by session_id → set of skill names already returned in full.
_skills_seen_by_session: dict[str, set[str]] = {}


def _vision_cache_dir() -> Path:
    """On-disk cache for full vision_analyze outputs that were truncated inline."""
    root = Path(os.environ.get("BOTJI_VISION_CACHE_DIR", "/opt/data/vision_cache"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _persist_vision_full(content: str) -> Path:
    """Write the full vision_analyze output to a content-hashed file. Idempotent."""
    digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
    out = _vision_cache_dir() / f"{digest}.txt"
    if not out.exists():
        out.write_text(content, encoding="utf-8")
    return out


def truncate_vision_analyze(result: str, session_id: str) -> str | None:
    """Return a truncated replacement for vision_analyze when the result is large.

    Returns None when no truncation is needed (small results pass through unchanged).
    """
    if not isinstance(result, str) or len(result) <= _VISION_INLINE_MAX:
        return None
    try:
        path = _persist_vision_full(result)
    except Exception:
        logger.exception("truncate_vision_analyze: failed to persist full output")
        path = None
    head = result[:_VISION_INLINE_MAX].rstrip()
    suffix = f"\n\n[vision_analyze: truncated from {len(result)} chars; full body at {path}]" if path else \
             f"\n\n[vision_analyze: truncated from {len(result)} chars]"
    logger.info("truncate_vision_analyze: %d → %d chars (session=%s)",
                len(result), len(head) + len(suffix), session_id or "?")
    return head + suffix


def truncate_skill_view(result: str, args: dict[str, Any], session_id: str) -> str | None:
    """Dedupe skill_view results within a session.

    First read returns full content (subject to _SKILL_INLINE_MAX). Subsequent reads
    of the same skill name in the same session return a short reminder. Returns None
    when no rewrite is needed.
    """
    if not isinstance(result, str) or not session_id:
        return None
    skill_name = str((args or {}).get("name") or (args or {}).get("skill") or "")
    if not skill_name:
        # Try to recover the name from the result payload header.
        m = re.search(r"name:\s*(\S+)", result[:500])
        skill_name = m.group(1).strip() if m else ""
    seen = _skills_seen_by_session.setdefault(session_id, set())
    if skill_name and skill_name in seen:
        msg = (
            f"[skill_view: '{skill_name}' already loaded earlier in this session. "
            "The full content is in the conversation history above. "
            "Do not re-read skills you have already seen — use what you remember.]"
        )
        logger.info("truncate_skill_view: dedup '%s' (session=%s)", skill_name, session_id)
        return msg
    if skill_name:
        seen.add(skill_name)
    # Cap first reads at _SKILL_INLINE_MAX even on first call.
    if len(result) > _SKILL_INLINE_MAX:
        head = result[:_SKILL_INLINE_MAX].rstrip()
        suffix = f"\n\n[skill '{skill_name}' truncated from {len(result)} chars; key sections preserved]"
        logger.info("truncate_skill_view: '%s' %d → %d chars (session=%s)",
                    skill_name, len(result), len(head) + len(suffix), session_id)
        return head + suffix
    return None


def truncate_generic(tool_name: str, result: str, session_id: str) -> str | None:
    """Catch-all for any other tool returning a wildly large payload."""
    if not isinstance(result, str) or len(result) <= _GENERIC_INLINE_MAX:
        return None
    head = result[:_GENERIC_INLINE_MAX].rstrip()
    suffix = f"\n\n[{tool_name}: truncated from {len(result)} chars]"
    logger.info("truncate_generic: %s %d → %d chars (session=%s)",
                tool_name, len(result), len(head) + len(suffix), session_id or "?")
    return head + suffix


# Tools that bypass truncation — their full output is needed by downstream logic.
_PASSTHROUGH_TOOLS = frozenset({
    "artifact_register",     # artifact records are small but referenced by ID
    "artifact_read",         # explicit read intent — don't truncate
    "artifact_review",       # processed by the verdict writer; replay needs full content
    "artifact_transform",    # output payload is small (path + metadata)
    "artifact_normalize",    # schema payload — must stay intact for comparators
    "artifact_extract",      # evidence IDs only — small
    "artifact_list",         # small
})


def transform(tool_name: str, args: dict, result: Any, session_id: str) -> str | None:
    """Single entry point for the transform_tool_result hook.

    Returns either a truncated/replacement string or None to pass through unchanged.
    """
    if tool_name in _PASSTHROUGH_TOOLS:
        return None
    if not isinstance(result, str):
        return None
    if tool_name == "vision_analyze":
        return truncate_vision_analyze(result, session_id)
    if tool_name == "skill_view":
        return truncate_skill_view(result, args or {}, session_id)
    return truncate_generic(tool_name, result, session_id)


def reset_session(session_id: str) -> None:
    """Forget which skills have been seen for a session. Called on /new or idle reset."""
    _skills_seen_by_session.pop(session_id, None)


__all__ = ["transform", "reset_session"]
