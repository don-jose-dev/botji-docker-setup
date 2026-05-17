"""botji-render plugin: gpt-image-2 with lease + fair-share queue + timeout.

Registers a single tool, ``botji_render``, that the agent calls instead of
the legacy ``artifact_transform`` with ``operation=edit_image``. The tool
is registered with ``override=False`` (it has its own unique name); the
agent prompt directs the agent to prefer it over the legacy route.

The plugin is intentionally narrow: it does one thing (render an image
under bounded contention) and does it well. The artifact bookkeeping
(register / extract / normalize / list / read) stays in botji-artifacts;
the review pipeline lives in botji-gate. This separation lets us swap
each layer independently.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Add plugin dir to sys.path so sibling modules resolve.
_PLUGIN_DIR = str(Path(__file__).parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from _render import render  # noqa: E402

logger = logging.getLogger(__name__)


_TOOL_SCHEMA = {
    "name": "botji_render",
    "description": (
        "Render a source image with gpt-image-2 under a hard timeout, "
        "fair-share queue across users, and 3-attempt parameter mutation. "
        "Returns a structured success or failure payload. Prefer this over "
        "artifact_transform(operation=edit_image) for any image-edit request "
        "originating from a user-attached photo."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source_image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
                "description": "Absolute paths to source images (must be readable by the hermes user).",
            },
            "prompt": {
                "type": "string",
                "minLength": 8,
                "description": (
                    "Edit instruction. Structure: subject preservation rules first, "
                    "then change description, then explicit FORBIDDEN list. Repeat "
                    "preservation language on every retry."
                ),
            },
            "user_id": {
                "type": "string",
                "description": (
                    "Caller user ID for lease + fair-share keying. The gateway "
                    "supplies this; the agent should pass it through verbatim."
                ),
            },
            "size": {
                "type": "string",
                "description": "Output size (e.g. '1024x1536'). Match source aspect for layout fidelity.",
                "default": "1024x1536",
            },
            "quality": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "high",
            },
            "input_fidelity": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "high",
            },
            "output_format": {
                "type": "string",
                "enum": ["png", "jpeg", "webp"],
                "default": "png",
            },
        },
        "required": ["source_image_paths", "prompt", "user_id"],
    },
}


def _handle_botji_render(args: dict[str, Any], **_: Any) -> str:
    """Synchronous adapter for the hermes tool registry.

    The render coroutine runs on a fresh event loop because hermes tool
    handlers are invoked synchronously from a worker thread. Returns the
    rendered image as a base64 data string plus structured metadata so
    the agent can decide whether to ship the result or retry.
    """
    try:
        source_paths = [Path(p) for p in args.get("source_image_paths") or []]
        if not source_paths:
            return _json({"success": False, "error": "source_image_paths is required", "error_kind": "validation"})
        for p in source_paths:
            if not p.exists():
                return _json({
                    "success": False,
                    "error": f"source path does not exist: {p}",
                    "error_kind": "validation",
                })
        prompt = str(args.get("prompt") or "").strip()
        if len(prompt) < 8:
            return _json({"success": False, "error": "prompt is too short", "error_kind": "validation"})
        user_id = str(args.get("user_id") or "").strip()
        if not user_id:
            return _json({"success": False, "error": "user_id is required", "error_kind": "validation"})

        client = _resolve_codex_client()

        result = asyncio.run(render(
            client=client,
            user_id=user_id,
            source_image_paths=source_paths,
            prompt=prompt,
            size=str(args.get("size") or "1024x1536"),
            quality=str(args.get("quality") or "high"),
            input_fidelity=str(args.get("input_fidelity") or "high"),
            output_format=str(args.get("output_format") or "png"),
        ))
        # Trim image_b64 from the returned-to-agent payload — the agent
        # doesn't need 2 MB of base64 in its context. Persist it to disk
        # and return a path the artifact_register tool can pick up.
        if result.get("success") and "image_b64" in result:
            output_path = _persist_image_bytes(
                base64.b64decode(result["image_b64"]),
                user_id=user_id,
                fmt=str(args.get("output_format") or "png"),
            )
            result = {**{k: v for k, v in result.items() if k != "image_b64"}, "output_path": str(output_path)}
        return _json(result)
    except Exception as exc:  # programmer errors / unexpected failures
        logger.exception("botji_render: unhandled exception")
        return _json({
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "error_kind": "internal",
        })


def _json(payload: dict[str, Any]) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False, default=str)


def _persist_image_bytes(data: bytes, *, user_id: str, fmt: str) -> Path:
    """Write the rendered image to /opt/data/artifacts/outputs and return path."""
    import uuid
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_id = f"art_{ts}_{uuid.uuid4().hex[:8]}"
    root = Path(os.environ.get("BOTJI_ARTIFACT_ROOT", "/opt/data/artifacts"))
    out_dir = root / "outputs" / artifact_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"render.{fmt}"
    out_path.write_bytes(data)
    return out_path


def _resolve_codex_client() -> Any:
    """Resolve the openai-codex Client via hermes auxiliary helpers.

    Falls back to importing from agent.auxiliary_client if available;
    raises a clear error if Codex auth is missing so the failure mode
    is "tool error to agent" rather than "silent stall".
    """
    try:
        from agent.auxiliary_client import resolve_provider_client  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"hermes auxiliary_client unavailable: {exc}") from exc
    client = resolve_provider_client("openai-codex")
    if client is None:
        raise RuntimeError(
            "openai-codex client not available — run `make codex-push-auth` to seed auth"
        )
    return client


def register(ctx) -> None:
    """Plugin entry point invoked by hermes_cli.plugins."""
    ctx.register_tool(
        name="botji_render",
        toolset="file",
        schema=_TOOL_SCHEMA,
        handler=_handle_botji_render,
        description=_TOOL_SCHEMA["description"],
    )
    logger.info("botji-render: registered tool 'botji_render'")
