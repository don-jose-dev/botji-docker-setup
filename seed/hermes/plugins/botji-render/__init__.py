"""botji-render plugin: gpt-image-2 with lease + fair-share queue + timeout.

Registers:
  - BotjiEditProvider (ImageGenProvider) for hermes-native txt2img routing
  - botji_render tool for img2img requests with source artifacts and the full
    lease+queue pipeline

The plugin is intentionally narrow: it does one thing (render an image
under bounded contention) and does it well. The artifact bookkeeping
(register / extract / normalize / list / read) stays in botji-artifacts;
the review pipeline lives in botji-gate. This separation lets us swap
each layer independently.
"""
from __future__ import annotations

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

from _render import render, _CODEX_CHAT_MODEL  # noqa: E402

# Lazy import — agent.image_gen_provider is hermes-internal; we want the plugin
# to still load (registering the botji_render tool) even if the ABC moves.
try:
    from agent.image_gen_provider import ImageGenProvider as _ImageGenProvider  # type: ignore
except Exception:
    _ImageGenProvider = object  # type: ignore

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
            "output_format": {
                "type": "string",
                "enum": ["png", "jpeg", "webp"],
                "default": "png",
            },
        },
        "required": ["source_image_paths", "prompt", "user_id"],
    },
}


class BotjiEditProvider(_ImageGenProvider):
    """Registers botji as a hermes-native image gen provider for txt2img requests.

    Separate from the botji_render tool (which handles img2img with source artifacts
    and the full lease+queue pipeline). This provider handles standalone image
    creation requests from the image_generate tool.
    """

    @property
    def name(self) -> str:
        return "botji-codex"

    @property
    def display_name(self) -> str:
        return "Botji (Codex auth)"

    def is_available(self) -> bool:
        try:
            from agent.auxiliary_client import _read_codex_access_token
            return bool(_read_codex_access_token())
        except Exception:
            return False

    def list_models(self) -> list[dict]:
        return [
            {"id": "gpt-image-2-high",   "display": "GPT Image 2 (High)",   "speed": "~2min",  "strengths": "Highest fidelity"},
            {"id": "gpt-image-2-medium", "display": "GPT Image 2 (Medium)", "speed": "~40s",   "strengths": "Balanced"},
            {"id": "gpt-image-2-low",    "display": "GPT Image 2 (Low)",    "speed": "~15s",   "strengths": "Fast iteration"},
        ]

    def default_model(self) -> str:
        return "gpt-image-2-high"

    def get_setup_schema(self) -> dict:
        return {
            "name": "Botji (Codex auth)",
            "badge": "codex",
            "tag": "gpt-image-2 via ChatGPT/Codex OAuth",
            "env_vars": [],
            "post_setup_hint": "Run `hermes auth codex` to authenticate.",
        }

    def generate(self, prompt: str, aspect_ratio: str = "landscape", **kwargs) -> dict:
        from agent.image_gen_provider import (
            resolve_aspect_ratio, save_b64_image, success_response, error_response
        )
        aspect = resolve_aspect_ratio(aspect_ratio)
        _sizes = {"landscape": "1536x1024", "square": "1024x1024", "portrait": "1024x1536"}
        size = _sizes.get(aspect, "1024x1024")

        model_id = kwargs.get("model") or self.default_model()
        quality = model_id.rsplit("-", 1)[-1] if "-" in model_id else "high"
        if quality not in ("low", "medium", "high"):
            quality = "high"

        try:
            client = _build_render_client()
        except Exception as exc:
            return error_response(error=str(exc), error_type="auth_required",
                                  provider=self.name, prompt=prompt, aspect_ratio=aspect)

        content = [{"type": "input_text", "text": prompt}]
        try:
            b64 = _sync_generate_txt2img(client, content=content, size=size,
                                         quality=quality, output_format="png")
        except Exception as exc:
            return error_response(error=f"Codex image generation failed: {exc}",
                                  error_type="api_error", provider=self.name,
                                  model=model_id, prompt=prompt, aspect_ratio=aspect)

        if not b64:
            return error_response(error="Provider returned empty image",
                                  error_type="empty_response", provider=self.name,
                                  model=model_id, prompt=prompt, aspect_ratio=aspect)

        try:
            path = save_b64_image(b64, prefix=f"botji_{quality}")
        except Exception as exc:
            return error_response(error=f"Could not save image: {exc}",
                                  error_type="io_error", provider=self.name,
                                  model=model_id, prompt=prompt, aspect_ratio=aspect)

        return success_response(image=str(path), model=model_id, prompt=prompt,
                                aspect_ratio=aspect, provider=self.name,
                                extra={"size": size, "quality": quality})


def _sync_generate_txt2img(
    client: Any,
    *,
    content: list[dict[str, Any]],
    size: str,
    quality: str,
    output_format: str,
) -> str | None:
    """Synchronous txt2img call via Responses API. No source images in content."""
    image_b64: str | None = None
    with client.responses.stream(
        model=_CODEX_CHAT_MODEL,
        store=False,
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


async def _handle_botji_render(args: dict[str, Any], **_: Any) -> str:
    """Async handler registered with is_async=True — hermes bridges via _run_async().

    Do NOT wrap in asyncio.run() — hermes gateway already has a running event loop
    and calling asyncio.run() from within it raises RuntimeError. The is_async=True
    flag on register_tool tells the framework to await this coroutine instead.
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

        client = _build_render_client()

        result = await render(
            client=client,
            user_id=user_id,
            source_image_paths=source_paths,
            prompt=prompt,
            size=str(args.get("size") or "1024x1536"),
            quality=str(args.get("quality") or "high"),
            output_format=str(args.get("output_format") or "png"),
        )
        if result.get("success") and "image_b64" in result:
            output_path = _persist_image_bytes(
                base64.b64decode(result["image_b64"]),
                user_id=user_id,
                fmt=str(args.get("output_format") or "png"),
            )
            result = {**{k: v for k, v in result.items() if k != "image_b64"}, "output_path": str(output_path)}
        return _json(result)
    except Exception as exc:
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
    try:
        from agent.image_gen_provider import save_b64_image
        return save_b64_image(base64.b64encode(data).decode(), prefix=f"botji_edit_{user_id[:8]}", extension=fmt)
    except Exception:
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


def _build_render_client() -> Any:
    """Build a raw openai.OpenAI client authenticated with Codex OAuth.

    Mirrors _build_codex_client() in the native openai-codex plugin.
    """
    try:
        from agent.auxiliary_client import (  # type: ignore
            _read_codex_access_token,
            _codex_cloudflare_headers,
        )
    except Exception as exc:
        raise RuntimeError(f"hermes auxiliary_client unavailable: {exc}") from exc
    token = _read_codex_access_token()
    if not token:
        raise RuntimeError("openai-codex token not available — run `hermes auth codex`")
    from openai import OpenAI
    return OpenAI(
        api_key=token,
        base_url="https://chatgpt.com/backend-api/codex",
        default_headers=_codex_cloudflare_headers(token),
    )


def register(ctx) -> None:
    ctx.register_image_gen_provider(BotjiEditProvider())
    ctx.register_tool(
        name="botji_render",
        toolset="file",
        schema=_TOOL_SCHEMA,
        handler=_handle_botji_render,
        description=_TOOL_SCHEMA["description"],
        is_async=True,
    )
    logger.info("botji-render: registered ImageGenProvider + botji_render tool")
