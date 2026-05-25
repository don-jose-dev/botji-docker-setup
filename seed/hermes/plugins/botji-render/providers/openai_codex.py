"""Codex image-generation provider for Botji render operations."""
from __future__ import annotations

import base64
import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


def _env(name: str, default: str) -> str:
    import os
    return os.environ.get(name, default)


API_MODEL = _env("BOTJI_IMAGE_MODEL", "gpt-image-2-high")
CODEX_CHAT_MODEL = _env("BOTJI_CODEX_IMAGE_CHAT_MODEL", "gpt-5.4-mini")


@lru_cache(maxsize=16)
def _load_prompt(name: str) -> str:
    path = Path(__file__).resolve().parents[1] / "prompts" / f"{name}.md"
    return path.read_text(encoding="utf-8").rstrip()


def _ensure_hermes_import_path() -> None:
    hermes_root = "/opt/hermes"
    if hermes_root not in sys.path:
        sys.path.insert(0, hermes_root)


def _read_codex_access_token() -> str | None:
    _ensure_hermes_import_path()
    try:
        from agent.auxiliary_client import _read_codex_access_token as _reader

        token = _reader()
        if isinstance(token, str) and token.strip():
            return token.strip()
    except Exception:
        return None
    return None


def codex_available() -> bool:
    return bool(_read_codex_access_token())


def _build_codex_client() -> Any | None:
    token = _read_codex_access_token()
    if not token:
        return None
    _ensure_hermes_import_path()
    from agent.auxiliary_client import _codex_cloudflare_headers
    from openai import OpenAI

    return OpenAI(
        api_key=token,
        base_url=CODEX_BASE_URL,
        default_headers=_codex_cloudflare_headers(token),
    )


def resolve_provider_route(requested: str) -> str:
    route = (requested or "auto").strip().lower().replace("-", "_")
    if route == "codex":
        route = "openai_codex"
    if route not in {"auto", "openai_codex"}:
        raise ValueError("provider_route must be auto or openai_codex")
    if route == "auto":
        if codex_available():
            return "openai_codex"
        raise RuntimeError("No image provider route is available: Codex OAuth token is missing")
    return route


def _data_url(path: Path, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _round_to_16(value: int) -> int:
    return max(16, (int(value) // 16) * 16)


def _normalize_codex_size(size: str) -> str:
    if not isinstance(size, str):
        return "1024x1024"
    parts = size.strip().lower().split("x")
    if len(parts) != 2:
        return size.strip()
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return size.strip()
    return f"{_round_to_16(width)}x{_round_to_16(height)}"


def _codex_size_for_sources(size: str, sources: list[Path]) -> str:
    if isinstance(size, str) and size.strip().lower() != "auto":
        return _normalize_codex_size(size)
    try:
        from PIL import Image

        with Image.open(sources[0]) as image:
            width, height = image.size
        if width > height * 1.08:
            return "1536x1024"
        if height > width * 1.08:
            return "1024x1536"
    except Exception:
        pass
    return "1024x1024"


def _collect_codex_image_b64(
    client: Any,
    *,
    content: list[dict[str, Any]],
    size: str,
    quality: str,
    output_format: str,
) -> str | None:
    image_b64: str | None = None
    with client.responses.stream(
        model=CODEX_CHAT_MODEL,
        store=False,
        instructions=_load_prompt("image_system"),
        input=[{
            "type": "message",
            "role": "user",
            "content": content,
        }],
        tools=[{
            "type": "image_generation",
            "model": API_MODEL,
            "size": size,
            "quality": "high" if quality == "auto" else quality,
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
            event_type = getattr(event, "type", "")
            if event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "image_generation_call":
                    result = getattr(item, "result", None)
                    if isinstance(result, str) and result:
                        image_b64 = result
            elif event_type == "response.image_generation_call.partial_image":
                partial = getattr(event, "partial_image_b64", None)
                if isinstance(partial, str) and partial:
                    image_b64 = partial
        final = stream.get_final_response()

    for item in getattr(final, "output", None) or []:
        if getattr(item, "type", None) == "image_generation_call":
            result = getattr(item, "result", None)
            if isinstance(result, str) and result:
                image_b64 = result
    return image_b64


def generate_image(
    sources: list[Path],
    policy: Any,
    *,
    output_dir: Path,
    **kwargs: Any,
) -> Any:
    """Generate an image with the ChatGPT/Codex OAuth-backed image provider."""
    from operations import RenderResult  # type: ignore[import-not-found]

    client = _build_codex_client()
    if client is None:
        return RenderResult(
            operation="edit_image",
            output_path=None,
            error="No Codex/ChatGPT OAuth token available. Run Hermes/Codex auth before using provider_route=openai_codex.",
            error_type="RuntimeError",
            metadata={"provider": "openai-codex"},
        )

    prompt = str(kwargs.get("prompt") or "").strip()
    if not prompt:
        return RenderResult(
            operation="edit_image",
            output_path=None,
            error="prompt is required",
            error_type="ValueError",
            metadata={"provider": "openai-codex"},
        )

    source_artifacts = kwargs.get("source_artifacts")
    artifacts = source_artifacts if isinstance(source_artifacts, list) else []
    quality = str(kwargs.get("quality") or "high")
    requested_size = str(kwargs.get("size") or getattr(policy, "target_size", None) or "auto")
    output_format = str(kwargs.get("output_format") or "png")
    fidelity_mode = str(kwargs.get("fidelity_mode") or "strict")
    contract_id = str(kwargs.get("contract_id") or "manual")
    resolved_size = _codex_size_for_sources(requested_size, sources)

    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": (
            _load_prompt("fidelity_baseline")
            + "\n\n"
            + _load_prompt("image_generation")
                .replace("{{FIDELITY_MODE}}", fidelity_mode)
                .replace("{{INSTRUCTIONS}}", prompt)
        ),
    }]
    for index, source in enumerate(sources, start=1):
        artifact = artifacts[index - 1] if index - 1 < len(artifacts) and isinstance(artifacts[index - 1], dict) else {}
        artifact_id = artifact.get("artifact_id") or source.name
        mime_type = artifact.get("detected_type") or "image/png"
        content.append({"type": "input_text", "text": f"Source image {index}: {artifact_id}"})
        content.append({"type": "input_image", "image_url": _data_url(source, mime_type)})

    b64_json = _collect_codex_image_b64(
        client,
        content=content,
        size=resolved_size,
        quality=quality,
        output_format=output_format,
    )
    if not b64_json:
        return RenderResult(
            operation="edit_image",
            output_path=None,
            error="Codex Responses image_generation response did not include an image result",
            error_type="RuntimeError",
            metadata={"provider": "openai-codex"},
        )

    extension = "jpg" if output_format == "jpeg" else output_format
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"output.{extension}"
    output_path.write_bytes(base64.b64decode(b64_json))

    return RenderResult(
        operation="edit_image",
        output_path=output_path,
        metadata={
            "provider": "openai-codex",
            "model": API_MODEL,
            "chat_model": CODEX_CHAT_MODEL,
            "endpoint": "codex.responses.stream:image_generation",
            "route": "artifact_transform.edit_image.openai_codex",
            "source_count": len(sources),
            "source_paths": [str(source) for source in sources],
            "source_artifact_ids": [artifact.get("artifact_id") for artifact in artifacts if isinstance(artifact, dict)],
            "source_sha256s": [artifact.get("sha256") for artifact in artifacts if isinstance(artifact, dict)],
            "source_input_mode": "input_image",
            "prompt": prompt,
            "quality": quality,
            "size": resolved_size,
            "requested_size": requested_size,
            "output_format": output_format,
            "fidelity_mode": fidelity_mode,
            "contract_id": contract_id,
            "input_fidelity_omitted": API_MODEL == "gpt-image-2",
            "input_fidelity_rationale": "gpt-image-2 processes image inputs at high fidelity automatically; input_fidelity is intentionally omitted.",
            "no_prompt_only_fallback": True,
        },
    )
