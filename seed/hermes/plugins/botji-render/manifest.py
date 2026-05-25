"""Spatial manifest extraction via the Codex/ChatGPT Responses API.

Migrated out of ``botji-artifacts/_codex.py`` for V1R PR 11. Given an image
path, returns a typed spatial manifest (scene type, modality, elements,
adjacency/opening constraints, layout hints, fidelity requirements).

Public entry point::

    extract_manifest(image_path: Path, mime_type: str | None = None) -> dict

Returns ``{"provider": "openai-codex", "model": <chat_model>, "manifest": {...}}``.

Failure modes:

- No Codex/ChatGPT OAuth token available → ``RuntimeError`` (no fallback).
- Model returns no parseable JSON → ``RuntimeError`` with truncated body.
- Network/transport errors are raised as-is so the caller can decide retry.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from providers.openai_codex import (  # type: ignore[import-not-found]
    CODEX_CHAT_MODEL,
    _build_codex_client,
    _data_url,
    _load_prompt,
)


_MANIFEST_JSON_SCHEMA: dict[str, Any] = {
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
        "opening_constraints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "string"},
                    "room_or_zone": {"type": "string"},
                    "opening_type": {"type": "string"},
                    "wall_or_side": {"type": "string"},
                    "position_on_wall": {"type": "string"},
                    "swing_or_handing": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "element_id", "room_or_zone", "opening_type", "wall_or_side",
                    "position_on_wall", "swing_or_handing", "notes",
                ],
                "additionalProperties": False,
            },
        },
        "layout_hints": {"type": "array", "items": {"type": "string"}},
        "fidelity_requirements": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "scene_type", "source_modality", "elements", "element_count",
        "adjacency_constraints", "opening_constraints", "layout_hints",
        "fidelity_requirements",
    ],
    "additionalProperties": False,
}


def extract_manifest(image_path: Path, mime_type: str | None = None) -> dict[str, Any]:
    """Extract a structured spatial manifest from a source image.

    Uses OpenAI structured outputs (strict JSON schema) so the response is
    always valid JSON matching the manifest shape — no regex fallback needed.
    Falls back to prompt-only parsing if the model does not support structured
    output (older Codex builds).
    """
    client = _build_codex_client()
    if client is None:
        raise RuntimeError("No Codex/ChatGPT OAuth token available for manifest extraction")

    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": _load_prompt("manifest_extract")},
        {"type": "input_image", "image_url": _data_url(image_path, mime_type or "image/png")},
    ]
    instructions = (
        "You are a spatial-analysis assistant. Extract structured manifests from images. "
        "Return only valid JSON matching the provided schema exactly."
    )

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


__all__ = ["extract_manifest"]
