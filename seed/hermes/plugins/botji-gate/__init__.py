"""botji-gate plugin: Default-FAIL delivery gate via transform_llm_output.

This is the architectural enforcement that promotes the today-shipped
``delivery_gate_notice`` patch from advisory (agent has to read the
notice and choose to comply) to authoritative (the framework rewrites
the response if the gate is closed).

Pattern (from Anthropic's cwc-long-running-agents reference):

  1. Each fidelity-sensitive turn writes a ``review-verdict.json`` file
     under the session work dir. Every axis starts ``false``.
  2. A reviewer (subagent in a separate context; see botji-reviewer.yaml)
     flips axes to ``true`` only on positive evidence.
  3. The ``transform_llm_output`` hook reads the file just before the
     response is sent to the user. If any axis is ``false``, the hook
     replaces the assistant text with a structured "retry needed" message
     and the response carries the failure detail to the user.

The agent cannot ship a blocked artifact because the framework rewrites
the response after the agent's last word. No agent compliance required.

The gate is **opt-in per-turn**: a verdict file must exist for the gate
to engage. Plain text turns (greetings, chit-chat) skip the gate entirely
because no file is written. This avoids gating every response — only
turns that opted into the fidelity contract by writing the file.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Verdict files live under the artifact root so they survive container
# recreate alongside artifacts and reviews.
_VERDICT_ROOT = Path(os.environ.get("BOTJI_VERDICT_ROOT", "/opt/data/verdicts"))

# How fresh a verdict file must be to count for this turn. Stale files
# from a prior turn must not gate the current response.
_VERDICT_MAX_AGE_SECONDS = 600.0  # 10 minutes


def _verdict_path_for_session(session_id: str) -> Path:
    """One verdict file per session, deterministic naming."""
    return _VERDICT_ROOT / f"{session_id}.verdict.json"


def _read_fresh_verdict(session_id: str) -> dict[str, Any] | None:
    """Return the verdict dict if one exists and is fresh; else None.

    Stale verdicts (>10 min old) are ignored so the gate doesn't block on
    an artifact from a prior turn that has since been superseded.
    """
    path = _verdict_path_for_session(session_id)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    age = time.time() - stat.st_mtime
    if age > _VERDICT_MAX_AGE_SECONDS:
        logger.info("gate: ignoring stale verdict %s (age=%.0fs)", path, age)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("gate: failed to parse %s: %s", path, exc)
        return None


def _format_block_message(verdict: dict[str, Any]) -> str:
    """User-facing message when delivery is blocked.

    Short, actionable, mentions which axes failed. The agent gets a
    chance to retry on the next turn with the failed-axis details.
    """
    failed_axes = [
        name for name, value in (verdict.get("axes") or {}).items()
        if value is not True
    ]
    primary = verdict.get("primary_blocker") or (
        failed_axes[0] if failed_axes else "fidelity review"
    )
    lines = [
        "⏳ I'm not done yet — the last render didn't pass review.",
        f"Blocker: *{primary}*.",
    ]
    if failed_axes:
        lines.append(f"Axes still failing: {', '.join(failed_axes)}.")
    guidance = verdict.get("retry_guidance")
    if guidance:
        lines.append(f"\n{guidance}")
    else:
        lines.append("\nLet me try again — send 'retry' or refine the request.")
    return "\n".join(lines)


def _transform_llm_output(
    response_text: str,
    *,
    session_id: str | None = None,
    **_: Any,
) -> str | None:
    """Hook: rewrite or block the outgoing assistant message.

    Returns:
        A replacement string when the gate is closed; None to pass through
        unchanged (no verdict file → no gate; verdict all-true → no gate).
    """
    if not session_id:
        return None
    verdict = _read_fresh_verdict(session_id)
    if verdict is None:
        return None  # no fidelity contract for this turn
    delivery_gate = str(verdict.get("delivery_gate") or "").lower()
    if delivery_gate == "clear":
        return None
    if delivery_gate == "warned":
        # Pass through but prepend a short caveat.
        return f"⚠️ {response_text}\n\n_(Review noted minor caveats; see /review for details.)_"
    if delivery_gate == "blocked":
        logger.info("gate: BLOCKED delivery for session=%s", session_id)
        return _format_block_message(verdict)
    return None  # unknown gate value → pass through


def register(ctx) -> None:
    """Plugin entry point — register the transform_llm_output hook."""
    _VERDICT_ROOT.mkdir(parents=True, exist_ok=True)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
    logger.info(
        "botji-gate: transform_llm_output gate registered (verdict_root=%s, max_age=%.0fs)",
        _VERDICT_ROOT,
        _VERDICT_MAX_AGE_SECONDS,
    )
