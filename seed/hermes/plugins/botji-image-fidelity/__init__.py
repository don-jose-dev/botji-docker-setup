"""Botji source-image fidelity plugin.

This plugin adds an agent-facing ``image_edit`` tool for workflows where the
source image itself is evidence. Hermes' built-in ``image_generate`` tool is
prompt-only; this tool sends the referenced image files to OpenAI's image edit
endpoint so layout, object identity, and visible geometry are not collapsed to
prose before generation.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable, List, Optional


API_MODEL = "gpt-image-2"
MAX_IMAGES = 16
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_QUALITIES = {"low", "medium", "high", "auto"}
ALLOWED_SIZES = {"auto", "1024x1024", "1536x1024", "1024x1536"}
ALLOWED_FORMATS = {"png", "jpeg", "webp"}
SECRET_PARTS = {".codex", ".ssh", ".gnupg", ".config"}
SECRET_FILE_NAMES = {".env", "auth.json", "credentials.json", "id_rsa", "id_ed25519"}
SECRET_KEYWORDS = ("secret", "token", "credential", "password", "api_key", "apikey")


IMAGE_EDIT_SCHEMA = {
    "name": "image_edit",
    "description": (
        "Edit or transform one or more source images with GPT Image 2. Use this "
        "instead of prompt-only image_generate when the user asks to preserve an "
        "uploaded/source image, make a 3D/rendered version, revise a layout, "
        "change style while keeping structure, or otherwise maintain visual "
        "fidelity. Requires OPENAI_API_KEY. Returns a local image path."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Edit instruction. State what to preserve, what may change, "
                    "and any forbidden drift from the source image."
                ),
            },
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": MAX_IMAGES,
                "description": (
                    "Absolute or /opt/data-relative source image paths. Use the "
                    "cached upload path when available, e.g. "
                    "/opt/data/cache/images/source.jpg."
                ),
            },
            "mask_path": {
                "type": "string",
                "description": "Optional mask image path for localized edits.",
            },
            "quality": {
                "type": "string",
                "enum": sorted(ALLOWED_QUALITIES),
                "default": "high",
                "description": "GPT Image 2 quality tier. Use high for fidelity work.",
            },
            "size": {
                "type": "string",
                "enum": sorted(ALLOWED_SIZES),
                "default": "auto",
                "description": "Output size. Use auto unless the contract requires orientation.",
            },
            "output_format": {
                "type": "string",
                "enum": sorted(ALLOWED_FORMATS),
                "default": "png",
                "description": "Output file format.",
            },
        },
        "required": ["prompt", "image_paths"],
    },
}


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()


def _workspace_root() -> Path:
    return Path(os.environ.get("BOTJI_WORKDIR", "/workspace")).resolve()


def _safe_roots() -> List[Path]:
    return [_hermes_home(), _workspace_root()]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _looks_secret(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    name = path.name.lower()
    if any(part in SECRET_PARTS for part in parts):
        return True
    if name in SECRET_FILE_NAMES:
        return True
    return any(keyword in name for keyword in SECRET_KEYWORDS)


def _resolve_image_path(raw: Any, *, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field} must be a non-empty string path")

    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        candidate = _hermes_home() / candidate

    path = candidate.resolve(strict=True)
    roots = _safe_roots()
    if not any(_is_relative_to(path, root) for root in roots):
        allowed = ", ".join(str(root) for root in roots)
        raise ValueError(f"{field} must be under one of: {allowed}")
    if _looks_secret(path):
        raise ValueError(f"{field} points at a protected credential-like path")
    if not path.is_file():
        raise ValueError(f"{field} is not a file: {path}")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
        raise ValueError(f"{field} must be an image file ({allowed}): {path}")
    return path


def _coerce_image_paths(value: Any) -> List[Path]:
    if isinstance(value, str):
        items: Iterable[Any] = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("image_paths must be a list of image paths")

    paths = [_resolve_image_path(item, field="image_paths") for item in items]
    if not paths:
        raise ValueError("image_paths must include at least one source image")
    if len(paths) > MAX_IMAGES:
        raise ValueError(f"image_paths supports at most {MAX_IMAGES} images")
    return paths


def _normalize_choice(value: Any, default: str, allowed: set[str], field: str) -> str:
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _artifact_root() -> Path:
    return Path(os.environ.get("BOTJI_ARTIFACT_ROOT", str(_hermes_home() / "artifacts"))).resolve()


def _save_b64_image(b64_json: str, *, quality: str, output_format: str) -> Path:
    extension = "jpg" if output_format == "jpeg" else output_format
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    output_dir = _artifact_root() / "outputs" / f"direct_image_edit_{stamp}_{suffix}"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"botji_image_edit_{API_MODEL}_{quality}.{extension}"
    path.write_bytes(base64.b64decode(b64_json))
    return path


def _extract_b64(response: Any) -> Optional[str]:
    data = getattr(response, "data", None)
    if not data:
        return None
    first = data[0]
    if isinstance(first, dict):
        return first.get("b64_json")
    return getattr(first, "b64_json", None)


def _openai_api_key_present() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _handle_image_edit(args: dict[str, Any], **_: Any) -> str:
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _json({
            "success": False,
            "image": None,
            "error": "prompt is required for image_edit",
            "error_type": "invalid_argument",
        })
    prompt = prompt.strip()

    if not _openai_api_key_present():
        return _json({
            "success": False,
            "image": None,
            "error": (
                "OPENAI_API_KEY is not configured. image_edit intentionally "
                "does not fall back to prompt-only generation for source-image "
                "fidelity work."
            ),
            "error_type": "auth_required",
            "provider": "openai",
            "model": API_MODEL,
        })

    try:
        source_paths = _coerce_image_paths(args.get("image_paths"))
        mask_path = args.get("mask_path")
        resolved_mask = (
            _resolve_image_path(mask_path, field="mask_path") if mask_path else None
        )
        quality = _normalize_choice(args.get("quality"), "high", ALLOWED_QUALITIES, "quality")
        size = _normalize_choice(args.get("size"), "auto", ALLOWED_SIZES, "size")
        output_format = _normalize_choice(
            args.get("output_format"), "png", ALLOWED_FORMATS, "output_format"
        )
    except Exception as exc:
        return _json({
            "success": False,
            "image": None,
            "error": str(exc),
            "error_type": "invalid_argument",
        })

    files = []
    mask_file = None
    try:
        from openai import OpenAI

        client = OpenAI()
        files = [path.open("rb") for path in source_paths]
        kwargs: dict[str, Any] = {
            "model": API_MODEL,
            "prompt": prompt,
            "image": files if len(files) > 1 else files[0],
            "quality": quality,
            "size": size,
            "output_format": output_format,
        }
        if resolved_mask is not None:
            mask_file = resolved_mask.open("rb")
            kwargs["mask"] = mask_file

        response = client.images.edit(**kwargs)
        b64_json = _extract_b64(response)
        if not b64_json:
            return _json({
                "success": False,
                "image": None,
                "error": "OpenAI image edit response did not include b64_json",
                "error_type": "empty_response",
                "provider": "openai",
                "model": API_MODEL,
            })

        saved_path = _save_b64_image(
            b64_json,
            quality=quality,
            output_format=output_format,
        )
        parent_ids = [str(path) for path in source_paths]
        parent_id = parent_ids[0] if len(parent_ids) == 1 else ";".join(parent_ids)
        return _json({
            "success": True,
            "image": str(saved_path),
            "provider": "openai",
            "model": API_MODEL,
            "endpoint": "images.edit",
            "quality": quality,
            "size": size,
            "output_format": output_format,
            "source_images": parent_ids,
            "mask": str(resolved_mask) if resolved_mask else None,
            "prompt": prompt,
            "lineage": {
                "parent_artifact_id": parent_id,
                "output_artifact_id": str(saved_path),
                "version_label": "Render v1",
                "change_reason": "source-image edit/reference generation",
            },
            "fidelity_note": (
                "Source image files were sent as image inputs. GPT Image 2 "
                "handles image inputs at high fidelity; no prompt-only fallback "
                "was used."
            ),
        })
    except Exception as exc:
        return _json({
            "success": False,
            "image": None,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "provider": "openai",
            "model": API_MODEL,
        })
    finally:
        for handle in files:
            try:
                handle.close()
            except Exception:
                pass
        if mask_file is not None:
            try:
                mask_file.close()
            except Exception:
                pass


def register(ctx) -> None:
    ctx.register_tool(
        name="image_edit",
        toolset="image_gen",
        schema=IMAGE_EDIT_SCHEMA,
        handler=_handle_image_edit,
        requires_env=["OPENAI_API_KEY"],
        description=IMAGE_EDIT_SCHEMA["description"],
    )
