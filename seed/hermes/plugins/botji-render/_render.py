"""gpt-image-2 via Codex Responses API with hard timeout, 3-attempt mutation,
and fair-share queueing.

gpt-image-2 is only reachable through the Codex Responses API
(client.responses.stream with tools=[{"type": "image_generation", ...}]).
The raw OpenAI images.edit() endpoint requires a direct API key, which
Codex OAuth does not provide. The correct client is a raw openai.OpenAI
instance built with the Codex access token + Cloudflare headers — NOT the
CodexAuxiliaryClient wrapper returned by resolve_provider_client(), which
only exposes chat.completions.

Failure modes addressed (all observed in production on 2026-05-17):

* Silent stall: Responses API stream has no built-in timeout. Wrapped in
  asyncio.wait_for() per attempt — on the 3rd timeout we return a structured
  {"success": false} so the agent can surface it to the user.

* Cross-user preemption: per-user SQLite leases namespace requests so a
  new turn cannot clobber an in-flight one; fair-share queuing prevents
  one user monopolising the provider.

* No retry intelligence: attempt 2 adds a determinism hint; attempt 3
  drops quality to medium so the user gets something rather than a third
  timeout.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
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

_ATTEMPT_TIMEOUTS_SECONDS = (180.0, 180.0, 90.0)
_PROVIDER_HARD_CEILING_SECONDS = 195.0
_CODEX_CHAT_MODEL = os.environ.get("BOTJI_CODEX_IMAGE_CHAT_MODEL", "gpt-5.4-mini")
_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


def _normalize_size(size: str) -> str:
    """gpt-image-2 requires width and height divisible by 16. Floor each
    dimension; the API returns HTTP 400 otherwise (e.g. '1536x883'). Falls
    back to 1024x1024 on parse failure."""
    if not isinstance(size, str):
        return "1024x1024"
    parts = size.strip().lower().split("x")
    if len(parts) != 2:
        return "1024x1024"
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        return "1024x1024"
    w = max(16, (w // 16) * 16)
    h = max(16, (h // 16) * 16)
    return f"{w}x{h}"


class RenderError(Exception):
    """Structured render failure; converted to tool error by the handler."""

    def __init__(self, message: str, *, attempt: int, kind: str):
        super().__init__(message)
        self.attempt = attempt
        self.kind = kind


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _load_prompt_safe(name: str) -> str:
    """Load a botji prompt template; return empty string on any failure."""
    prompts_dir = Path(os.environ.get("BOTJI_PROMPTS_DIR", "/opt/data/prompts"))
    try:
        return (prompts_dir / "templates" / f"{name}.md").read_text()
    except Exception:
        return ""


def _build_request_content(
    source_image_paths: list[Path],
    prompt: str,
    attempt: int,
) -> list[dict[str, Any]]:
    """Build Responses API content list, mutating prompt between attempts.

    Attempt 1: original prompt, optionally prefixed with premium baseline.
    Attempt 2: append a determinism hint — produces a different seed.
    Attempt 3: prepend minimal-change instruction — fast fallback.
    """
    baseline = _load_prompt_safe("premium_baseline")
    gen_template = _load_prompt_safe("image_generation")

    if gen_template:
        text = (
            ((baseline + "\n\n") if baseline else "")
            + gen_template.replace("{{INSTRUCTIONS}}", prompt)
        )
    else:
        text = ((baseline + "\n\n") if baseline else "") + prompt

    if attempt == 2:
        text += (
            "\n\nRefinement pass: keep the exact subject and layout from the source "
            "image; do not invent extra elements."
        )
    elif attempt == 3:
        text = (
            "Make minimal but visible changes to the source. "
            "Preserve every element from the source exactly.\n\n" + text
        )

    content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    for idx, path in enumerate(source_image_paths, start=1):
        content.append({"type": "input_text", "text": f"Source image {idx}:"})
        content.append({"type": "input_image", "image_url": _image_data_url(path)})
    return content


_DEFAULT_SYSTEM_INSTRUCTIONS = (
    "You are a source-bound image generation assistant. When the user provides input "
    "images, treat them as the spatial authority — match the layout, module order, and "
    "visible elements from the source exactly. Follow any structured brief (SUBJECT, HARD "
    "PRESERVE, FORBIDDEN sections) as binding constraints, not stylistic suggestions. "
    "Always call the image_generation tool. Never describe the image instead of generating it."
)


def _load_system_instructions() -> str:
    """Load the image_system prompt from disk, falling back to the inline default.

    The Codex Responses API requires the ``instructions`` parameter for image_generation
    calls — omitting it returns HTTP 400 'Instructions are required'. We previously left
    this off and every botji_render call failed at the provider. The plugin's own
    ``prompts/image_system.md`` lives under botji-artifacts; we duplicate it via env or
    fall back to the inline default so the tool is self-contained.
    """
    prompts_dir = Path(os.environ.get("BOTJI_PROMPTS_DIR", "/opt/data/prompts"))
    try:
        text = (prompts_dir / "templates" / "image_system.md").read_text().strip()
        if text:
            return text
    except Exception:
        pass
    return _DEFAULT_SYSTEM_INSTRUCTIONS


def _sync_generate(
    client: Any,
    *,
    content: list[dict[str, Any]],
    size: str,
    quality: str,
    output_format: str,
) -> str | None:
    """Synchronous Responses API streaming call. Run via asyncio.to_thread."""
    image_b64: str | None = None
    with client.responses.stream(
        model=_CODEX_CHAT_MODEL,
        store=False,
        instructions=_load_system_instructions(),
        input=[{"type": "message", "role": "user", "content": content}],
        tools=[{
            "type": "image_generation",
            "model": "gpt-image-2",
            "size": size,
            "quality": quality if quality != "auto" else "high",
            "output_format": output_format,
            "background": "opaque",
            "partial_images": 1,
        }],
        tool_choice={
            "type": "allowed_tools",
            "mode": "required",
            "tools": [{"type": "image_generation"}],
        },
    ) as stream:
        for event in stream:
            if getattr(event, "type", "") == "response.output_item.done":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "image_generation_call":
                    result = getattr(item, "result", None)
                    if isinstance(result, str) and result:
                        image_b64 = result
        final = stream.get_final_response()

    for item in getattr(final, "output", None) or []:
        if getattr(item, "type", None) == "image_generation_call":
            result = getattr(item, "result", None)
            if isinstance(result, str) and result:
                image_b64 = result
    return image_b64


async def _call_provider_once(
    client: Any,
    *,
    content: list[dict[str, Any]],
    size: str,
    quality: str,
    output_format: str,
    timeout_seconds: float,
) -> str:
    """Run one Responses API image generation with a hard timeout.

    Returns the base64 image string on success; raises RenderError on
    timeout or provider error.
    """
    try:
        image_b64 = await asyncio.wait_for(
            asyncio.to_thread(
                _sync_generate, client,
                content=content,
                size=size,
                quality=quality,
                output_format=output_format,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise RenderError(
            f"gpt-image-2 stream did not return within {timeout_seconds:.0f}s",
            attempt=-1,
            kind="timeout",
        ) from exc
    except Exception as exc:
        raise RenderError(
            f"gpt-image-2 provider error: {type(exc).__name__}: {exc}",
            attempt=-1,
            kind="provider_error",
        ) from exc
    return image_b64  # type: ignore[return-value]


async def render(
    *,
    client: Any,
    user_id: str,
    source_image_paths: list[Path],
    prompt: str,
    size: str = "1024x1536",
    quality: str = "high",
    output_format: str = "png",
) -> dict[str, Any]:
    """Render a source image with gpt-image-2 under lease + fair-share + timeout.

    On success returns ``{"success": True, "image_b64": ..., "attempts": N,
    "queue_wait_seconds": X, "render_seconds": Y, "from_cache": False}``.

    On exhaustion returns ``{"success": False, "error": "...",
    "error_kind": "timeout"|"provider_error", "attempts": 3}``.
    """
    size = _normalize_size(size)
    request_hash = compute_request_hash(
        prompt, size, quality, output_format,
        [str(p) for p in source_image_paths],
    )

    cached = lookup_result(user_id, request_hash)
    if cached:
        logger.info("render: cached result user=%s hash=%s", user_id, request_hash[:8])
        return {**cached, "from_cache": True}

    holder = f"{user_id}:{os.getpid()}:{time.time():.0f}"

    with acquire(user_id, request_hash, holder=holder) as got_lease:
        if not got_lease:
            for _ in range(30):
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

        async with reserve_slot(user_id) as queue_wait:
            render_start = time.monotonic()
            last_error: RenderError | None = None

            for attempt_idx in range(3):
                attempt_num = attempt_idx + 1
                quality_for_attempt = "medium" if attempt_num == 3 else quality
                content = _build_request_content(source_image_paths, prompt, attempt_num)
                timeout = min(_ATTEMPT_TIMEOUTS_SECONDS[attempt_idx], _PROVIDER_HARD_CEILING_SECONDS)

                logger.info(
                    "render attempt %d/3 user=%s timeout=%.0fs quality=%s",
                    attempt_num, user_id, timeout, quality_for_attempt,
                )
                try:
                    image_b64 = await _call_provider_once(
                        client,
                        content=content,
                        size=size,
                        quality=quality_for_attempt,
                        output_format=output_format,
                        timeout_seconds=timeout,
                    )
                except RenderError as exc:
                    exc.attempt = attempt_num
                    last_error = exc
                    logger.warning(
                        "render attempt %d/3 failed user=%s kind=%s: %s",
                        attempt_num, user_id, exc.kind, exc,
                    )
                    continue

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
                }
                record_result(user_id, request_hash, payload)
                return payload

            payload = {
                "success": False,
                "error": str(last_error) if last_error else "gpt-image-2 failed after 3 attempts",
                "error_kind": last_error.kind if last_error else "unknown",
                "attempts": 3,
                "queue_wait_seconds": round(queue_wait, 2),
                "render_seconds": round(time.monotonic() - render_start, 2),
            }
            record_result(user_id, request_hash, payload)
            return payload


__all__ = ["render", "RenderError"]
