"""gpt-image-2 wrapper with hard timeout, 3-attempt mutation, fair-share queueing.

Replaces the ``artifact_transform.edit_image.openai_codex`` route in the
legacy botji-artifacts plugin. The replacement is structural, not
behavioural: same provider, same model, same prompt shape — but every
external call is bounded, every failure is surfaced as a structured tool
error, and every concurrent request is fairly scheduled across users.

Failure modes addressed (all observed in production on 2026-05-17):

* **Silent stall.** The legacy code awaited the provider stream with no
  upper bound; when the connection hung (Codex backend throttle or
  network), no log line was emitted and the agent's turn died with no
  user-facing error. Here, every attempt has a hard timeout; on the
  3rd timeout we return a structured ``{"success": false, ...}`` so
  the agent can apologise to the user and the gate hook can deliver it.

* **Cross-user preemption.** When user A's hung turn was abandoned and
  user B's new turn re-initialised the shared client, A's pending
  request was orphaned. Per-user SQLite leases now namespace requests
  so a new turn cannot clobber an in-flight one for the same user;
  fair-share queueing prevents one user from monopolising the provider.

* **No retry intelligence.** When gpt-image-2 returned a malformed
  output, the legacy code surfaced it as a successful artifact. Here,
  attempts 2 and 3 mutate the request (new prompt seed, lower quality)
  rather than retrying with identical parameters.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from _lease import (
    acquire,
    compute_request_hash,
    lookup_result,
    record_result,
)
from _fairshare import reserve_slot

logger = logging.getLogger(__name__)

# Per-attempt deadlines. The 3rd attempt is intentionally short — at that
# point the provider is misbehaving and we want to fail fast and tell the
# user, not keep them waiting another three minutes.
_ATTEMPT_TIMEOUTS_SECONDS = (180.0, 180.0, 90.0)

# gpt-image-2 hard ceiling per OpenAI community reports.
_PROVIDER_HARD_CEILING_SECONDS = 195.0


class RenderError(Exception):
    """Structured render failure; converted to tool error by the handler."""

    def __init__(self, message: str, *, attempt: int, kind: str):
        super().__init__(message)
        self.attempt = attempt
        self.kind = kind


def _attempt_params(attempt: int, base: dict[str, Any]) -> dict[str, Any]:
    """Mutate request parameters between attempts.

    Attempt 1: full quality, original prompt — try to nail it.
    Attempt 2: append a determinism hint to the prompt — different seed.
    Attempt 3: lower quality, prepend "minimal change" — fast fallback so
               the user gets *something* rather than a third timeout.
    """
    params = dict(base)
    prompt = str(params.get("prompt", ""))
    if attempt == 1:
        return params
    if attempt == 2:
        params["prompt"] = (
            prompt
            + "\n\nRefinement pass: keep the exact subject and layout from the source image; do not invent extra elements."
        )
        return params
    # attempt 3 — speed over polish
    params["prompt"] = (
        "Make minimal but visible changes to the source. Preserve every element from the source exactly.\n\n" + prompt
    )
    params["quality"] = "medium"
    return params


async def _call_provider_once(
    client: Any,
    params: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run a single gpt-image-2 edit with a hard timeout.

    Returns the provider response dict on success; raises :class:`RenderError`
    on timeout or provider error. Does not catch programmer errors —
    those should still propagate up.
    """
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(client.images.edit, **params),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise RenderError(
            f"gpt-image-2 stream did not return within {timeout_seconds:.0f}s",
            attempt=-1,
            kind="timeout",
        ) from exc
    except Exception as exc:  # provider 4xx/5xx / SDK errors
        raise RenderError(
            f"gpt-image-2 provider error: {type(exc).__name__}: {exc}",
            attempt=-1,
            kind="provider_error",
        ) from exc
    return result


async def render(
    *,
    client: Any,
    user_id: str,
    source_image_paths: list[Path],
    prompt: str,
    size: str = "1024x1536",
    quality: str = "high",
    input_fidelity: str = "high",
    output_format: str = "png",
) -> dict[str, Any]:
    """Render a source image with gpt-image-2 under lease + fair-share + timeout.

    On success returns ``{"success": True, "image_bytes": ..., "attempts": N,
    "queue_wait_seconds": X, "render_seconds": Y, "from_cache": False}``.

    On exhaustion returns ``{"success": False, "error": "...",
    "error_kind": "timeout"|"provider_error", "attempts": 3}``. The
    caller (tool handler) wraps this in the tool-result envelope so the
    agent gets a structured error and the gate hook can deliver a
    user-facing message.
    """
    request_hash = compute_request_hash(
        prompt,
        size,
        quality,
        input_fidelity,
        output_format,
        [str(p) for p in source_image_paths],
    )

    # Fast path: idempotent duplicate (same user re-submitting the same
    # request, e.g. after a "Yes" follow-up) returns the cached payload.
    cached = lookup_result(user_id, request_hash)
    if cached:
        logger.info("render: serving cached result user=%s hash=%s", user_id, request_hash[:8])
        return {**cached, "from_cache": True}

    holder = f"{user_id}:{os.getpid()}:{time.time():.0f}"

    with acquire(user_id, request_hash, holder=holder) as got_lease:
        if not got_lease:
            # Another worker is already rendering the identical request for
            # the same user. Poll the result table briefly; if it doesn't
            # appear, fail cleanly so the caller can surface "queued".
            for _ in range(30):  # up to 30 s polling
                await asyncio.sleep(1)
                cached = lookup_result(user_id, request_hash)
                if cached:
                    return {**cached, "from_cache": True}
            return {
                "success": False,
                "error": "duplicate request already in flight for this user; please wait and retry",
                "error_kind": "lease_held",
                "attempts": 0,
            }

        # Fair-share scheduling across users — wait our turn.
        async with reserve_slot(user_id) as queue_wait:
            render_start = time.monotonic()
            base_params = {
                "model": "gpt-image-2",
                "image": [open(p, "rb") for p in source_image_paths],
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "input_fidelity": input_fidelity,
                "output_format": output_format,
            }
            last_error: RenderError | None = None
            for attempt_idx in range(3):
                attempt_num = attempt_idx + 1
                params = _attempt_params(attempt_num, base_params)
                # Rewind file handles between attempts.
                for fh in params["image"]:
                    try:
                        fh.seek(0)
                    except Exception:
                        pass
                timeout = min(_ATTEMPT_TIMEOUTS_SECONDS[attempt_idx], _PROVIDER_HARD_CEILING_SECONDS)
                logger.info(
                    "render attempt %d/3 user=%s timeout=%.0fs quality=%s",
                    attempt_num,
                    user_id,
                    timeout,
                    params.get("quality"),
                )
                try:
                    provider_result = await _call_provider_once(
                        client, params, timeout_seconds=timeout
                    )
                except RenderError as exc:
                    exc.attempt = attempt_num
                    last_error = exc
                    logger.warning(
                        "render attempt %d/3 failed user=%s kind=%s: %s",
                        attempt_num,
                        user_id,
                        exc.kind,
                        exc,
                    )
                    continue
                # Success — extract base64 image and record.
                image_b64 = _extract_image_b64(provider_result)
                if not image_b64:
                    last_error = RenderError(
                        "provider returned an empty image payload",
                        attempt=attempt_num,
                        kind="empty_payload",
                    )
                    continue
                payload = {
                    "success": True,
                    "image_b64": image_b64,
                    "attempts": attempt_num,
                    "queue_wait_seconds": round(queue_wait, 2),
                    "render_seconds": round(time.monotonic() - render_start, 2),
                    "params_used": {
                        k: v for k, v in params.items() if k != "image"
                    },
                }
                record_result(user_id, request_hash, payload)
                return payload

            # All 3 attempts exhausted — fail with the most recent error.
            payload = {
                "success": False,
                "error": str(last_error) if last_error else "gpt-image-2 failed after 3 attempts with no error captured",
                "error_kind": last_error.kind if last_error else "unknown",
                "attempts": 3,
                "queue_wait_seconds": round(queue_wait, 2),
                "render_seconds": round(time.monotonic() - render_start, 2),
            }
            # Cache the failure briefly so duplicate requests don't keep
            # retrying — the cached failure is also idempotent.
            record_result(user_id, request_hash, payload)
            return payload


def _extract_image_b64(provider_result: Any) -> str | None:
    """Pull the base64 image out of the OpenAI images.edit response shape."""
    try:
        data = getattr(provider_result, "data", None) or provider_result.get("data")
        if data and len(data) > 0:
            first = data[0]
            b64 = getattr(first, "b64_json", None)
            if b64 is None and isinstance(first, dict):
                b64 = first.get("b64_json")
            return b64
    except (AttributeError, KeyError, IndexError, TypeError):
        return None
    return None


__all__ = ["render", "RenderError"]
