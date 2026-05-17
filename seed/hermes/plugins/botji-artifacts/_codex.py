"""Codex OAuth client, image generation, provider routing, and vision comparison."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any
from _constants import (
    API_MODEL, CODEX_CHAT_MODEL, CODEX_BASE_URL,
)
from _utils import _artifact_root, _now, _new_id, _sha256
from _registry import _append_record, _store_evidence, _load_artifact
from _detect import _detect_type
from _prompts import load_prompt
from _vision import (
    _data_url, _vision_review_prompt,
)


def _openai_codex_image_generate(
    *,
    source_artifacts: list[dict[str, Any]],
    prompt: str,
    contract_id: str,
    quality: str,
    size: str,
    output_format: str,
    fidelity_mode: str,
) -> dict[str, Any]:
    client = _build_codex_client()
    if client is None:
        raise RuntimeError("No Codex/ChatGPT OAuth token available. Run Hermes/Codex auth before using provider_route=openai_codex.")

    resolved_size = _codex_size_for_sources(size, source_artifacts)
    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": (
            load_prompt("premium_baseline")
            + "\n\n"
            + load_prompt("image_generation")
                .replace("{{FIDELITY_MODE}}", fidelity_mode)
                .replace("{{INSTRUCTIONS}}", prompt)
        ),
    }]
    for index, artifact in enumerate(source_artifacts, start=1):
        content.append({"type": "input_text", "text": f"Source image {index}: {artifact['artifact_id']}"})
        content.append({
            "type": "input_image",
            "image_url": _data_url(Path(artifact["path"]), artifact.get("detected_type") or "image/png"),
        })

    b64_json = _collect_codex_image_b64(
        client,
        content=content,
        size=resolved_size,
        quality=quality,
        output_format=output_format,
    )
    if not b64_json:
        raise RuntimeError("Codex Responses image_generation response did not include an image result")

    output_id = _new_id("art")
    extension = "jpg" if output_format == "jpeg" else output_format
    output_dir = _artifact_root() / "outputs" / output_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"output.{extension}"
    output_path.write_bytes(base64.b64decode(b64_json))

    detected_type, adapter = _detect_type(output_path, "image")
    output_record = {
        "artifact_id": output_id,
        "role": "output",
        "path": str(output_path),
        "original_path": str(output_path),
        "original_filename": output_path.name,
        "detected_type": detected_type,
        "declared_type": "image",
        "adapter": adapter,
        "sha256": _sha256(output_path),
        "size_bytes": output_path.stat().st_size,
        "created_at": _now(),
        "authority": "agent_generated",
        "parents": [artifact["artifact_id"] for artifact in source_artifacts],
        "evidence_ids": [],
        "preview_paths": [],
        "user_intent": prompt,
        "risk_flags": [],
        "provider": "openai-codex",
        "model": API_MODEL,
        "chat_model": CODEX_CHAT_MODEL,
        "endpoint": "codex.responses.stream:image_generation",
        "quality": quality,
        "size": resolved_size,
        "requested_size": size,
        "output_format": output_format,
        "fidelity_mode": fidelity_mode,
        "contract_id": contract_id,
        "route": "artifact_transform.edit_image.openai_codex",
    }
    _append_record(output_record)
    route_evidence = _store_evidence(
        output_record,
        extractor="artifact_transform",
        claim_level="verified",
        summary="Output image was created through Codex OAuth Responses image_generation with source artifacts as input_image items.",
        data={
            "provider": "openai-codex",
            "model": API_MODEL,
            "chat_model": CODEX_CHAT_MODEL,
            "endpoint": "codex.responses.stream:image_generation",
            "source_artifact_ids": [artifact["artifact_id"] for artifact in source_artifacts],
            "source_paths": [artifact["path"] for artifact in source_artifacts],
            "source_sha256s": [artifact.get("sha256") for artifact in source_artifacts],
            "source_input_mode": "input_image",
            "output_artifact_id": output_id,
            "output_sha256": _sha256(output_path),
            "prompt": prompt,
            "quality": quality,
            "size": resolved_size,
            "requested_size": size,
            "output_format": output_format,
            "fidelity_mode": fidelity_mode,
            "contract_id": contract_id,
            "input_fidelity_omitted": API_MODEL == "gpt-image-2",
            "input_fidelity_rationale": "gpt-image-2 processes image inputs at high fidelity automatically; input_fidelity is intentionally omitted.",
            "no_prompt_only_fallback": True,
        },
    )
    return {
        "output_artifact": _load_artifact(output_id),
        "route_evidence": route_evidence,
        "provider": "openai-codex",
        "model": API_MODEL,
        "chat_model": CODEX_CHAT_MODEL,
        "endpoint": "codex.responses.stream:image_generation",
    }



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


def _codex_available() -> bool:
    return bool(_read_codex_access_token())


def _build_codex_client():
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


def _resolve_provider_route(requested: str) -> str:
    route = (requested or "auto").strip().lower().replace("-", "_")
    if route == "codex":
        route = "openai_codex"
    if route not in {"auto", "openai_codex"}:
        raise ValueError("provider_route must be auto or openai_codex")
    if route == "auto":
        if _codex_available():
            return "openai_codex"
        raise RuntimeError("No image provider route is available: Codex OAuth token is missing")
    return route


def _resolve_review_provider_route(requested: str, output: dict[str, Any]) -> str:
    route = (requested or "auto").strip().lower().replace("-", "_")
    if route == "codex":
        route = "openai_codex"
    if route not in {"auto", "openai_codex"}:
        raise ValueError("review_provider_route must be auto or openai_codex")
    if route == "auto":
        if output.get("provider") == "openai-codex" and _codex_available():
            return "openai_codex"
        if _codex_available():
            return "openai_codex"
        raise RuntimeError("No vision review route is available: Codex OAuth token is missing")
    return route


def _round_to_16(value: int) -> int:
    """gpt-image-2 requires width and height divisible by 16. Floor — never up
    past the original to avoid expanding the canvas."""
    return max(16, (int(value) // 16) * 16)


def _normalize_codex_size(size: str) -> str:
    """Coerce a free-form 'WxH' size string into a valid gpt-image-2 size.

    The API rejects sizes whose dimensions are not multiples of 16 with
    HTTP 400. Agents sometimes generate fractional aspects (e.g. 1536x883
    from a 16:9 source); normalise here so the request always succeeds.
    Returns the input unchanged if parsing fails — caller decides fallback.
    """
    if not isinstance(size, str):
        return "1024x1024"
    parts = size.strip().lower().split("x")
    if len(parts) != 2:
        return size.strip()
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        return size.strip()
    return f"{_round_to_16(w)}x{_round_to_16(h)}"


def _codex_size_for_sources(size: str, source_artifacts: list[dict[str, Any]]) -> str:
    if isinstance(size, str) and size.strip().lower() != "auto":
        return _normalize_codex_size(size)
    try:
        from PIL import Image

        with Image.open(source_artifacts[0]["path"]) as image:
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
        instructions=load_prompt("image_system"),
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


_MANIFEST_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "scene_type": {
            "type": "string",
            "enum": [
                "kitchen_interior", "wardrobe_interior", "living_room", "bathroom",
                "bedroom", "office", "retail", "exterior", "product",
                "generic_interior", "unknown",
            ],
        },
        "source_modality": {
            "type": "string",
            "enum": ["sketch", "floor_plan", "photo", "render", "schematic"],
        },
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "wall_or_zone": {"type": "string"},
                    "position_index": {"type": "integer"},
                    "notes": {"type": "string"},
                },
                "required": ["id", "label", "wall_or_zone", "position_index", "notes"],
                "additionalProperties": False,
            },
        },
        "element_count": {"type": "integer"},
        "adjacency_constraints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "elem_a": {"type": "string"},
                    "elem_b": {"type": "string"},
                    "relation": {"type": "string"},
                    "wall_or_zone": {"type": "string"},
                },
                "required": ["elem_a", "elem_b", "relation", "wall_or_zone"],
                "additionalProperties": False,
            },
        },
        "layout_hints": {"type": "array", "items": {"type": "string"}},
        "fidelity_requirements": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "scene_type", "source_modality", "elements", "element_count",
        "adjacency_constraints", "layout_hints", "fidelity_requirements",
    ],
    "additionalProperties": False,
}


def _codex_extract_manifest(artifact: dict[str, Any]) -> dict[str, Any]:
    """Extract a structured spatial manifest from an image using vision.

    Uses OpenAI structured outputs (strict JSON schema) so the response is
    always valid JSON matching the manifest shape — no regex fallback needed.
    Falls back to prompt-only parsing if the model doesn't support structured
    output (older Codex builds).
    """
    client = _build_codex_client()
    if client is None:
        raise RuntimeError("No Codex/ChatGPT OAuth token available for manifest extraction")

    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": load_prompt("manifest_extract")},
        {
            "type": "input_image",
            "image_url": _data_url(
                Path(artifact["path"]),
                artifact.get("detected_type") or "image/png",
            ),
        },
    ]
    instructions = (
        "You are a spatial-analysis assistant. Extract structured manifests from images. "
        "Return only valid JSON matching the provided schema exactly."
    )

    # Attempt structured output first (strict JSON schema — no parsing needed).
    manifest: dict[str, Any] | None = None
    try:
        response = client.responses.create(
            model=CODEX_CHAT_MODEL,
            store=False,
            instructions=instructions,
            input=[{"type": "message", "role": "user", "content": content}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "spatial_manifest",
                    "schema": _MANIFEST_JSON_SCHEMA,
                    "strict": True,
                }
            },
        )
        text = getattr(response, "output_text", None) or ""
        if text:
            manifest = json.loads(text)
    except Exception:
        # Structured output not supported by this model/endpoint — fall through
        # to the streaming text path below.
        pass

    if manifest is None:
        # Fallback: streaming with prompt-based JSON extraction.
        text_parts: list[str] = []
        with client.responses.stream(
            model=CODEX_CHAT_MODEL,
            store=False,
            instructions=instructions,
            input=[{"type": "message", "role": "user", "content": content}],
        ) as stream:
            for event in stream:
                delta = getattr(event, "delta", None)
                if isinstance(delta, str):
                    text_parts.append(delta)
            response = stream.get_final_response()
        text = getattr(response, "output_text", None) or "".join(text_parts)
        if not text:
            raise RuntimeError("manifest extraction returned no text")
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        try:
            manifest = json.loads(stripped)
        except Exception:
            s, e = stripped.find("{"), stripped.rfind("}")
            if s != -1 and e > s:
                try:
                    manifest = json.loads(stripped[s:e + 1])
                except Exception:
                    pass
        if not manifest:
            raise RuntimeError(f"manifest extraction returned unparseable output: {text[:200]!r}")

    return {
        "provider": "openai-codex",
        "model": CODEX_CHAT_MODEL,
        "manifest": manifest,
    }


def _codex_vision_compare(sources: list[dict[str, Any]], output: dict[str, Any], fidelity_requirements: list[str]) -> dict[str, Any]:
    client = _build_codex_client()
    if client is None:
        raise RuntimeError("No Codex/ChatGPT OAuth token available for vision review")
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": _vision_review_prompt(fidelity_requirements),
        }
    ]
    for index, artifact in enumerate(sources, start=1):
        content.append({"type": "input_text", "text": f"Source image {index}: {artifact['artifact_id']}"})
        content.append({"type": "input_image", "image_url": _data_url(Path(artifact["path"]), artifact.get("detected_type") or "image/png")})
    content.append({"type": "input_text", "text": f"Output image: {output['artifact_id']}"})
    content.append({"type": "input_image", "image_url": _data_url(Path(output["path"]), output.get("detected_type") or "image/png")})
    text_parts: list[str] = []
    with client.responses.stream(
        model=CODEX_CHAT_MODEL,
        store=False,
        instructions=load_prompt("vision_compare"),
        input=[{
            "type": "message",
            "role": "user",
            "content": content,
        }],
    ) as stream:
        for event in stream:
            delta = getattr(event, "delta", None)
            if isinstance(delta, str):
                text_parts.append(delta)
        response = stream.get_final_response()

    text = getattr(response, "output_text", None) or "".join(text_parts)
    if not text:
        text = str(response)
    return {
        "provider": "openai-codex",
        "model": CODEX_CHAT_MODEL,
        "endpoint": "codex.responses.create",
        "comparison": text,
    }

