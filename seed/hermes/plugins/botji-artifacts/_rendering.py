"""Output artifact creation, schema markdown, and PNG schema preview rendering."""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import mimetypes
import os
import re
import shutil
import struct
import sys
import uuid
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET
import wave
import zipfile
from _constants import ARTIFACT_SCHEMA_VERSION
from _utils import _json, _now, _new_id, _sha256, _artifact_root
from _detect import _detect_type
from _registry import _append_record, _load_artifact, _write_json


def _create_output_artifact(
    *,
    output_id: str,
    output_path: Path,
    declared_type: str,
    parents: list[str],
    user_intent: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    detected_type, adapter = _detect_type(output_path, declared_type)
    record = {
        "artifact_id": output_id,
        "role": "output",
        "path": str(output_path),
        "original_path": str(output_path),
        "original_filename": output_path.name,
        "detected_type": detected_type,
        "declared_type": declared_type,
        "adapter": adapter,
        "sha256": _sha256(output_path),
        "size_bytes": output_path.stat().st_size,
        "created_at": _now(),
        "authority": "derived",
        "parents": parents,
        "evidence_ids": [],
        "preview_paths": [],
        "user_intent": user_intent,
        "risk_flags": [],
    }
    record.update(extra)
    _append_record(record)
    return record


def _resolve_schema_payload(
    source_artifacts: list[dict[str, Any]],
    schema_evidence_id: str,
    schema: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if schema:
        payload = schema
        if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            payload = {
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "profile": payload.get("profile", "inline_schema"),
                "artifact": {"artifact_id": source_artifacts[0]["artifact_id"]} if source_artifacts else {},
                "deterministic": {},
                "semantic_schema": payload,
                "fidelity_contract": {"hard_requirements": [], "advisory_preferences": [], "claim_boundary": "Inline schema supplied."},
                "transform_policy": {"preferred_routes": ["render_schema"], "forbidden_routes": []},
                "evidence_basis": [],
            }
        return payload, {"source": "inline_schema"}

    if schema_evidence_id:
        evidence = _load_evidence(schema_evidence_id)
        data = evidence.get("data")
        if not isinstance(data, dict) or data.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            raise ValueError(f"schema_evidence_id is not a {ARTIFACT_SCHEMA_VERSION} payload: {schema_evidence_id}")
        return data, evidence

    if not source_artifacts:
        raise ValueError("render_schema requires at least one source artifact")
    artifact = source_artifacts[0]
    payload = _build_normalized_schema(
        artifact,
        schema_profile="auto",
        intent="render_schema",
        semantic_schema={},
        hard_requirements=[],
        advisory_preferences=[],
        evidence_ids=[],
    )
    return payload, {"source": "auto_normalized"}


def _load_evidence(evidence_id: str) -> dict[str, Any]:
    if not evidence_id:
        raise ValueError("evidence_id is required")
    evidence_root = _artifact_root() / "evidence"
    for path in evidence_root.glob(f"*/{evidence_id}.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid evidence JSON: {path}") from exc
    raise ValueError(f"evidence not found: {evidence_id}")


def _schema_to_markdown(schema_payload: dict[str, Any], title: str) -> str:
    artifact = schema_payload.get("artifact") or {}
    deterministic = schema_payload.get("deterministic") or {}
    contract = schema_payload.get("fidelity_contract") or {}
    lines = [
        f"# {title}",
        "",
        f"- Schema: `{schema_payload.get('schema_version', ARTIFACT_SCHEMA_VERSION)}`",
        f"- Profile: `{schema_payload.get('profile', 'unknown')}`",
        f"- Artifact: `{artifact.get('artifact_id', 'unknown')}`",
        f"- Adapter: `{artifact.get('adapter', 'unknown')}`",
        f"- Detected type: `{artifact.get('detected_type', 'unknown')}`",
        f"- SHA-256: `{artifact.get('sha256', 'unknown')}`",
        "",
        "## Deterministic Evidence",
        "",
    ]
    for key, value in deterministic.items():
        if key in {"preview", "first_page_text_preview"}:
            continue
        lines.append(f"- `{key}`: {json.dumps(value, ensure_ascii=False)[:500]}")
    if contract.get("hard_requirements"):
        lines.extend(["", "## Hard Requirements", ""])
        lines.extend(f"- {item}" for item in contract.get("hard_requirements") or [])
    if contract.get("advisory_preferences"):
        lines.extend(["", "## Advisory Preferences", ""])
        lines.extend(f"- {item}" for item in contract.get("advisory_preferences") or [])
    return "\n".join(lines) + "\n"


def _render_schema_preview_png(schema_payload: dict[str, Any], output_path: Path, title: str) -> None:
    semantic = schema_payload.get("semantic_schema") or {}
    if isinstance(semantic.get("render_primitives"), list):
        _render_primitives_schema_preview_png(schema_payload, output_path, title)
    else:
        _render_generic_schema_preview_png(schema_payload, output_path, title)


def _render_generic_schema_preview_png(schema_payload: dict[str, Any], output_path: Path, title: str) -> None:
    from PIL import Image, ImageDraw

    width, height = 1400, 900
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, width - 40, height - 40), outline=(30, 30, 30), width=3)
    draw.text((70, 70), title, fill=(0, 0, 0))
    artifact = schema_payload.get("artifact") or {}
    deterministic = schema_payload.get("deterministic") or {}
    contract = schema_payload.get("fidelity_contract") or {}
    lines = [
        f"schema: {schema_payload.get('schema_version', ARTIFACT_SCHEMA_VERSION)}",
        f"profile: {schema_payload.get('profile', 'unknown')}",
        f"artifact: {artifact.get('artifact_id', 'unknown')}",
        f"adapter: {artifact.get('adapter', 'unknown')}",
        f"type: {artifact.get('detected_type', 'unknown')}",
        f"sha256: {str(artifact.get('sha256', ''))[:24]}...",
    ]
    for key in ("width", "height", "page_count", "line_count", "char_count", "dxfversion", "insunits", "size_bytes"):
        if key in deterministic:
            lines.append(f"{key}: {deterministic[key]}")
    hard = contract.get("hard_requirements") or []
    if hard:
        lines.append("")
        lines.append("hard requirements:")
        lines.extend(f"- {item[:110]}" for item in hard[:10])
    y = 130
    for line in lines:
        draw.text((70, y), line, fill=(20, 20, 20))
        y += 30
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _render_primitives_schema_preview_png(schema_payload: dict[str, Any], output_path: Path, title: str) -> None:
    from PIL import Image, ImageDraw

    semantic = schema_payload.get("semantic_schema") or {}
    canvas = semantic.get("canvas") if isinstance(semantic.get("canvas"), dict) else {}
    width = _safe_int(canvas.get("width"), 1600)
    height = _safe_int(canvas.get("height"), 950)
    background = _rgb(canvas.get("background"), (255, 255, 255))
    image = Image.new("RGB", (width, height), "white")
    if background != (255, 255, 255):
        image.paste(background, (0, 0, width, height))
    draw = ImageDraw.Draw(image)
    draw.text((40, 25), title, fill=(0, 0, 0))
    draw.text((40, 52), "Deterministic schema primitive preview. Domain meaning lives in schema data, not renderer code.", fill=(70, 70, 70))

    for primitive in semantic.get("render_primitives") or []:
        if not isinstance(primitive, dict):
            continue
        _draw_primitive(draw, primitive, width, height)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _draw_primitive(draw: Any, primitive: dict[str, Any], canvas_width: int, canvas_height: int) -> None:
    shape = str(primitive.get("shape") or primitive.get("type") or "").lower()
    fill = _rgb(primitive.get("fill"), None)
    outline = _rgb(primitive.get("outline") or primitive.get("stroke"), (60, 60, 60))
    text_fill = _rgb(primitive.get("text_fill"), (20, 20, 20))
    width = _safe_int(primitive.get("width") or primitive.get("stroke_width"), 2)
    if shape == "rect":
        draw.rectangle(_primitive_box(primitive, canvas_width, canvas_height), fill=fill, outline=outline, width=width)
    elif shape == "ellipse":
        draw.ellipse(_primitive_box(primitive, canvas_width, canvas_height), fill=fill, outline=outline, width=width)
    elif shape == "line":
        points = _primitive_points(primitive, canvas_width, canvas_height)
        if len(points) >= 2:
            draw.line(points[:2], fill=outline, width=width)
    elif shape == "polygon":
        points = _primitive_points(primitive, canvas_width, canvas_height)
        if len(points) >= 3:
            draw.polygon(points, fill=fill, outline=outline)
    elif shape == "text":
        x = _coord(primitive.get("x", 0), canvas_width)
        y = _coord(primitive.get("y", 0), canvas_height)
        draw.text((x, y), str(primitive.get("text") or primitive.get("label") or ""), fill=text_fill)
    if primitive.get("label") and shape != "text" and bool(primitive.get("show_label", True)):
        box = _primitive_box(primitive, canvas_width, canvas_height) if shape in {"rect", "ellipse"} else None
        if box:
            draw.text((box[0] + 5, box[1] + 5), str(primitive["label"])[:80], fill=text_fill)


def _primitive_box(primitive: dict[str, Any], canvas_width: int, canvas_height: int) -> tuple[int, int, int, int]:
    x = _coord(primitive.get("x", 0), canvas_width)
    y = _coord(primitive.get("y", 0), canvas_height)
    w = _coord(primitive.get("w", primitive.get("width_px", 0)), canvas_width)
    h = _coord(primitive.get("h", primitive.get("height_px", 0)), canvas_height)
    x2 = _coord(primitive.get("x2"), canvas_width) if primitive.get("x2") is not None else x + w
    y2 = _coord(primitive.get("y2"), canvas_height) if primitive.get("y2") is not None else y + h
    return (min(x, x2), min(y, y2), max(x, x2), max(y, y2))


def _primitive_points(primitive: dict[str, Any], canvas_width: int, canvas_height: int) -> list[tuple[int, int]]:
    raw_points = primitive.get("points")
    if isinstance(raw_points, list):
        points: list[tuple[int, int]] = []
        for point in raw_points:
            if isinstance(point, dict):
                points.append((_coord(point.get("x", 0), canvas_width), _coord(point.get("y", 0), canvas_height)))
            elif isinstance(point, list) and len(point) >= 2:
                points.append((_coord(point[0], canvas_width), _coord(point[1], canvas_height)))
        return points
    return [
        (_coord(primitive.get("x1", primitive.get("x", 0)), canvas_width), _coord(primitive.get("y1", primitive.get("y", 0)), canvas_height)),
        (_coord(primitive.get("x2", 0), canvas_width), _coord(primitive.get("y2", 0), canvas_height)),
    ]


def _coord(value: Any, extent: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if -1.0 <= number <= 1.0:
        return int(round(number * extent))
    return int(round(number))


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rgb(value: Any, default: tuple[int, int, int] | None) -> tuple[int, int, int] | None:
    if value is None:
        return default
    if isinstance(value, list) and len(value) >= 3:
        return tuple(max(0, min(255, int(component))) for component in value[:3])  # type: ignore[return-value]
    if isinstance(value, str):
        named = {
            "black": (0, 0, 0),
            "white": (255, 255, 255),
            "gray": (160, 160, 160),
            "grey": (160, 160, 160),
            "light_gray": (230, 230, 230),
            "light_grey": (230, 230, 230),
            "dark_gray": (80, 80, 80),
            "dark_grey": (80, 80, 80),
        }
        if value.lower() in named:
            return named[value.lower()]
        if value.startswith("#") and len(value) == 7:
            try:
                return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
            except ValueError:
                return default
    return default

