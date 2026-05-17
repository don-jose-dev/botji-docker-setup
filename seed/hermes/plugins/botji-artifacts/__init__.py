"""Botji generic artifact fidelity plugin.

The plugin turns files into durable Botji artifacts before the agent reasons
about them. It records checksums, adapter evidence, lineage, Codex-backed image
transforms, and review receipts.
"""

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


API_MODEL = os.environ.get("BOTJI_IMAGE_MODEL", "gpt-image-2")
VISION_REVIEW_MODEL = os.environ.get("BOTJI_VISION_REVIEW_MODEL", "gpt-5.4-mini")
CODEX_CHAT_MODEL = os.environ.get("BOTJI_CODEX_IMAGE_CHAT_MODEL", "gpt-5.4")
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_IMAGE_INSTRUCTIONS = (
    "You are an image-generation assistant. For source-bound requests, use "
    "the provided input image(s) as visual references and call the "
    "image_generation tool. Preserve requested layout, visible object identity, "
    "and source constraints. Do not ignore the input images."
)
MAX_SOURCE_ARTIFACTS = 16
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".css", ".js", ".ts", ".py"}
PDF_SUFFIXES = {".pdf"}
DXF_SUFFIXES = {".dxf"}
DOCX_SUFFIXES = {".docx"}
XLSX_SUFFIXES = {".xlsx", ".xlsm"}
HTML_SUFFIXES = {".html", ".htm"}
SVG_SUFFIXES = {".svg"}
STEP_SUFFIXES = {".step", ".stp"}
IFC_SUFFIXES = {".ifc"}
ZIP_SUFFIXES = {".zip"}
AUDIO_SUFFIXES = {".wav", ".wave", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
STRUCTURED_ADAPTERS = {
    "image",
    "text",
    "pdf",
    "dxf",
    "docx",
    "xlsx",
    "html",
    "svg",
    "step",
    "ifc",
    "zip",
    "audio",
    "video",
    "binary",
}
SECRET_PARTS = {".codex", ".ssh", ".gnupg", ".config"}
SECRET_FILE_NAMES = {".env", "auth.json", "credentials.json", "id_rsa", "id_ed25519"}
SECRET_KEYWORDS = ("secret", "token", "credential", "password", "api_key", "apikey")
CORE_REVIEW_AXES = [
    "source_coverage",
    "authority_alignment",
    "preserve_change",
    "groundedness",
    "uncertainty",
    "safety",
    "artifact_lineage",
    "actionability",
]
ARTIFACT_SCHEMA_VERSION = "botji.artifact_schema.v1"


def _tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": description, "parameters": parameters}


ARTIFACT_REGISTER_SCHEMA = _tool_schema(
    "artifact_register",
    "Register a local source/output file as a Botji artifact with checksum, type, adapter, and lineage.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path or HERMES_HOME-relative path."},
            "declared_type": {"type": "string", "default": "auto"},
            "role": {
                "type": "string",
                "enum": ["source", "reference", "intermediate", "output", "evidence", "preview"],
                "default": "source",
            },
            "authority": {
                "type": "string",
                "enum": ["user_supplied", "agent_generated", "external", "derived"],
                "default": "user_supplied",
            },
            "copy_into_registry": {"type": "boolean", "default": True},
            "parents": {"type": "array", "items": {"type": "string"}},
            "user_intent": {"type": "string"},
            "route": {
                "type": "string",
                "description": "Declared transform route for provider-generated outputs (e.g. artifact_transform.edit_image.openai_codex). Required when role=output and the artifact was produced by a provider tool rather than artifact_transform.",
            },
        },
        "required": ["path"],
    },
)

ARTIFACT_EXTRACT_SCHEMA = _tool_schema(
    "artifact_extract",
    "Extract deterministic metadata/evidence from a registered artifact using the selected adapter.",
    {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "detail": {"type": "string", "enum": ["metadata", "preview", "full"], "default": "metadata"},
            "intent": {"type": "string", "default": "review"},
            "adapter": {"type": "string", "default": "auto"},
        },
        "required": ["artifact_id"],
    },
)

ARTIFACT_NORMALIZE_SCHEMA = _tool_schema(
    "artifact_normalize",
    "Create a typed schema-first fidelity contract for any artifact adapter: image, PDF, text, DXF/CAD, DOCX, XLSX, HTML, SVG, STEP, IFC, ZIP, audio, video, or binary.",
    {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "schema_profile": {
                "type": "string",
                "default": "auto",
                "description": "Optional domain profile, such as cabinetry_layout, pdf_document, dxf_cad, text_document, or auto.",
            },
            "intent": {"type": "string", "default": "fidelity"},
            "semantic_schema": {
                "type": "object",
                "description": "Optional reviewed domain schema extracted from source content, such as modules, objects, dimensions, or line spans.",
            },
            "hard_requirements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Source facts or explicit user requirements that must pass review.",
            },
            "advisory_preferences": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Best-effort style or quality preferences that should warn, not block, unless made mandatory.",
            },
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Evidence IDs used to construct the normalized schema.",
            },
        },
        "required": ["artifact_id"],
    },
)

ARTIFACT_TRANSFORM_SCHEMA = _tool_schema(
    "artifact_transform",
    "Create a source-aware derivative artifact. Default behavior is byte-exact preservation; schema renders are deterministic; image edits require an explicit edit_image operation and use a real source-image provider route.",
    {
        "type": "object",
        "properties": {
            "contract_id": {"type": "string", "default": "manual"},
            "source_artifact_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "operation": {"type": "string", "enum": ["edit_image", "render_schema", "exact_copy"], "default": "exact_copy"},
            "instructions": {"type": "string"},
            "output_type": {"type": "string", "enum": ["image", "json", "text"], "default": "image"},
            "schema_evidence_id": {"type": "string", "description": "Evidence ID produced by artifact_normalize."},
            "schema": {"type": "object", "description": "Inline schema payload for deterministic render_schema transforms."},
            "render_title": {"type": "string", "default": "Botji artifact schema preview"},
            "fidelity_mode": {"type": "string", "enum": ["strict", "balanced", "creative"], "default": "strict"},
            "provider_route": {
                "type": "string",
                "enum": ["auto", "openai_codex"],
                "default": "auto",
                "description": "Use openai_codex for ChatGPT/Codex OAuth, or auto to require Codex OAuth when available.",
            },
            "quality": {"type": "string", "enum": ["low", "medium", "high", "auto"], "default": "high"},
            "size": {"type": "string", "default": "auto"},
            "output_format": {"type": "string", "enum": ["png", "jpeg", "webp"], "default": "png"},
        },
        "required": ["source_artifact_ids"],
    },
)

ARTIFACT_REVIEW_SCHEMA = _tool_schema(
    "artifact_review",
    "Persist a source-fidelity review receipt for generated artifacts. Can use Codex vision review for image pairs.",
    {
        "type": "object",
        "properties": {
            "contract_id": {"type": "string", "default": "manual"},
            "source_artifact_ids": {"type": "array", "items": {"type": "string"}},
            "output_artifact_id": {"type": "string"},
            "required_axes": {"type": "array", "items": {"type": "string"}},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "fidelity_requirements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hard source/content/layout requirements that the output must preserve from the source.",
            },
            "use_openai_vision": {"type": "boolean", "default": True},
            "require_vision_api": {"type": "boolean", "default": False},
            "review_provider_route": {
                "type": "string",
                "enum": ["auto", "openai_codex"],
                "default": "auto",
            },
        },
        "required": ["source_artifact_ids", "output_artifact_id"],
    },
)

ARTIFACT_LIST_SCHEMA = _tool_schema(
    "artifact_list",
    "List recent Botji artifacts from the registry.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "role": {"type": "string"},
            "adapter": {"type": "string"},
        },
    },
)

ARTIFACT_READ_SCHEMA = _tool_schema(
    "artifact_read",
    "Read one artifact registry record by artifact ID.",
    {
        "type": "object",
        "properties": {"artifact_id": {"type": "string"}},
        "required": ["artifact_id"],
    },
)


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()


def _workspace_root() -> Path:
    return Path(os.environ.get("BOTJI_WORKDIR", "/workspace")).resolve()


def _artifact_root() -> Path:
    return Path(os.environ.get("BOTJI_ARTIFACT_ROOT", str(_hermes_home() / "artifacts"))).resolve()


def _max_bytes() -> int:
    raw = os.environ.get("BOTJI_ARTIFACT_MAX_BYTES", "104857600")
    try:
        return max(1, int(raw))
    except ValueError:
        return 104857600


def _index_path() -> Path:
    return _artifact_root() / "index" / "artifacts.jsonl"


def _safe_roots() -> list[Path]:
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


def _resolve_allowed_path(raw: Any, *, field: str, must_exist: bool = True) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field} must be a non-empty path string")
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        candidate = _hermes_home() / candidate
    path = candidate.resolve(strict=must_exist)
    if not any(_is_relative_to(path, root) for root in _safe_roots()):
        allowed = ", ".join(str(root) for root in _safe_roots())
        raise ValueError(f"{field} must be under one of: {allowed}")
    if _looks_secret(path):
        raise ValueError(f"{field} points at a protected credential-like path")
    if must_exist and not path.is_file():
        raise ValueError(f"{field} is not a file: {path}")
    return path


def _safe_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in name)
    return cleaned[:120] or "artifact.bin"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_head(path: Path, size: int = 8192) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def _magic_mime(path: Path) -> str | None:
    try:
        import magic

        return magic.from_file(str(path), mime=True)
    except Exception:
        return None


def _detect_type(path: Path, declared_type: str = "auto") -> tuple[str, str]:
    suffix = path.suffix.lower()
    head = _read_head(path)
    mime = _magic_mime(path)
    declared = str(declared_type or "auto").lower()

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image"
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return "image/webp", "image"
    if head.startswith(b"RIFF") and b"WAVE" in head[:16]:
        return "audio/wav", "audio"
    if head.startswith(b"%PDF"):
        return "application/pdf", "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        zip_adapter = _detect_zip_adapter(path, suffix)
        if zip_adapter == "docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
        if zip_adapter == "xlsx":
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
        return "application/zip", "zip"
    if suffix in DXF_SUFFIXES or (b"SECTION" in head[:2048] and b"ENTITIES" in head[:8192]):
        return "image/vnd.dxf", "dxf"
    if suffix in IFC_SUFFIXES or _looks_ifc(head):
        return "application/x-step", "ifc"
    if suffix in STEP_SUFFIXES or _looks_step(head):
        return "model/step", "step"
    if suffix in SVG_SUFFIXES or _looks_svg(head):
        return "image/svg+xml", "svg"
    if suffix in HTML_SUFFIXES or _looks_html(head):
        return "text/html", "html"

    if mime and mime not in {"application/octet-stream", "text/plain"}:
        if mime.startswith("image/"):
            return mime, "image"
        if mime == "application/pdf":
            return mime, "pdf"
        if mime.startswith("audio/"):
            return mime, "audio"
        if mime.startswith("video/"):
            return mime, "video"
        if mime in {"text/html", "application/xhtml+xml"}:
            return mime, "html"
        if mime in {"image/svg+xml", "text/xml"} and suffix in SVG_SUFFIXES:
            return "image/svg+xml", "svg"
        if mime in {"application/zip", "application/x-zip-compressed"}:
            zip_adapter = _detect_zip_adapter(path, suffix)
            return mime, zip_adapter

    guessed, _ = mimetypes.guess_type(str(path))
    detected = mime or guessed or "application/octet-stream"
    if suffix in IMAGE_SUFFIXES or (detected and detected.startswith("image/")):
        return detected or "image/unknown", "image"
    if suffix in PDF_SUFFIXES:
        return "application/pdf", "pdf"
    if suffix in DOCX_SUFFIXES:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
    if suffix in XLSX_SUFFIXES:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    if suffix in HTML_SUFFIXES:
        return "text/html", "html"
    if suffix in SVG_SUFFIXES:
        return "image/svg+xml", "svg"
    if suffix in STEP_SUFFIXES:
        return detected if detected != "application/octet-stream" else "model/step", "step"
    if suffix in IFC_SUFFIXES:
        return detected if detected != "application/octet-stream" else "application/x-step", "ifc"
    if suffix in ZIP_SUFFIXES:
        return detected if detected != "application/octet-stream" else "application/zip", "zip"
    if suffix in AUDIO_SUFFIXES or detected.startswith("audio/"):
        return detected if detected != "application/octet-stream" else "audio/unknown", "audio"
    if suffix in VIDEO_SUFFIXES or detected.startswith("video/"):
        return detected if detected != "application/octet-stream" else "video/unknown", "video"
    if suffix in TEXT_SUFFIXES or _looks_text(head):
        return detected if detected.startswith("text/") else "text/plain", "text"
    if declared in STRUCTURED_ADAPTERS:
        return detected, declared
    return detected, "binary"


def _detect_zip_adapter(path: Path, suffix: str) -> str:
    if suffix in DOCX_SUFFIXES:
        return "docx"
    if suffix in XLSX_SUFFIXES:
        return "xlsx"
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except Exception:
        return "zip"
    if "[Content_Types].xml" in names and any(name.startswith("word/") for name in names):
        return "docx"
    if "[Content_Types].xml" in names and any(name.startswith("xl/") for name in names):
        return "xlsx"
    return "zip"


def _head_text(head: bytes) -> str:
    return head.decode("utf-8", errors="ignore").lstrip("\ufeff").strip().lower()


def _looks_html(head: bytes) -> bool:
    text = _head_text(head[:4096])
    return "<!doctype html" in text or "<html" in text or "<head" in text or "<body" in text


def _looks_svg(head: bytes) -> bool:
    text = _head_text(head[:4096])
    return "<svg" in text or ("http://www.w3.org/2000/svg" in text and "<" in text)


def _looks_step(head: bytes) -> bool:
    text = _head_text(head[:4096])
    return text.startswith("iso-10303-21") or "header;" in text and "file_schema" in text


def _looks_ifc(head: bytes) -> bool:
    text = _head_text(head[:8192])
    return _looks_step(head) and ("ifc" in text or "ifcproject" in text or "ifcsite" in text)


def _looks_text(head: bytes) -> bool:
    if not head:
        return True
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _load_records() -> list[dict[str, Any]]:
    path = _index_path()
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _append_record(record: dict[str, Any]) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _replace_record(artifact_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    records = _load_records()
    found: dict[str, Any] | None = None
    for record in records:
        if record.get("artifact_id") == artifact_id:
            record.update(updates)
            found = record
            break
    if found is None:
        raise ValueError(f"artifact not found: {artifact_id}")
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return found


def _load_artifact(artifact_id: str) -> dict[str, Any]:
    for record in _load_records():
        if record.get("artifact_id") == artifact_id:
            return record
    raise ValueError(f"artifact not found: {artifact_id}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _store_evidence(
    artifact: dict[str, Any],
    *,
    extractor: str,
    claim_level: str,
    summary: str,
    data: dict[str, Any],
    preview_paths: list[str] | None = None,
) -> dict[str, Any]:
    evidence_id = _new_id("ev")
    evidence_dir = _artifact_root() / "evidence" / artifact["artifact_id"]
    evidence_path = evidence_dir / f"{evidence_id}.json"
    payload = {
        "evidence_id": evidence_id,
        "artifact_id": artifact["artifact_id"],
        "adapter": artifact.get("adapter", "unknown"),
        "extractor": extractor,
        "created_at": _now(),
        "claim_level": claim_level,
        "summary": summary,
        "data": data,
        "data_path": str(evidence_path),
        "preview_paths": preview_paths or [],
    }
    _write_json(evidence_path, payload)
    evidence_ids = list(dict.fromkeys([*(artifact.get("evidence_ids") or []), evidence_id]))
    _replace_record(artifact["artifact_id"], {"evidence_ids": evidence_ids})
    return payload


def _register_path(
    path: Path,
    *,
    role: str,
    authority: str,
    declared_type: str = "auto",
    copy_into_registry: bool = True,
    parents: list[str] | None = None,
    user_intent: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    max_bytes = _max_bytes()
    size_bytes = path.stat().st_size
    if size_bytes > max_bytes:
        raise ValueError(f"artifact exceeds BOTJI_ARTIFACT_MAX_BYTES ({size_bytes} > {max_bytes})")

    artifact_id = _new_id("art")
    detected_type, adapter = _detect_type(path, declared_type)
    stored_path = path
    original_path = str(path)
    if copy_into_registry and role in {"source", "reference", "output"}:
        bucket = "outputs" if role == "output" else "sources"
        target_dir = _artifact_root() / bucket / artifact_id
        target_dir.mkdir(parents=True, exist_ok=True)
        stored_path = target_dir / _safe_filename(path.name)
        shutil.copy2(path, stored_path)

    record = {
        "artifact_id": artifact_id,
        "role": role,
        "path": str(stored_path),
        "original_path": original_path,
        "original_filename": path.name,
        "detected_type": detected_type,
        "declared_type": declared_type,
        "adapter": adapter,
        "sha256": _sha256(stored_path),
        "size_bytes": stored_path.stat().st_size,
        "created_at": _now(),
        "authority": authority,
        "parents": parents or [],
        "evidence_ids": [],
        "preview_paths": [],
        "user_intent": user_intent,
        "risk_flags": [],
    }
    if extra:
        record.update(extra)
    _append_record(record)
    return record


def _handle_artifact_register(args: dict[str, Any], **_: Any) -> str:
    try:
        source = _resolve_allowed_path(args.get("path"), field="path")
        route = str(args.get("route") or "").strip()
        extra: dict[str, Any] | None = {"route": route} if route else None
        record = _register_path(
            source,
            role=str(args.get("role") or "source"),
            authority=str(args.get("authority") or "user_supplied"),
            declared_type=str(args.get("declared_type") or "auto"),
            copy_into_registry=bool(args.get("copy_into_registry", True)),
            parents=[str(item) for item in args.get("parents") or []],
            user_intent=str(args.get("user_intent") or ""),
            extra=extra,
        )
        return _json({"success": True, "artifact": record})
    except Exception as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _handle_artifact_list(args: dict[str, Any], **_: Any) -> str:
    limit = int(args.get("limit") or 20)
    role = args.get("role")
    adapter = args.get("adapter")
    records = _load_records()
    if role:
        records = [record for record in records if record.get("role") == role]
    if adapter:
        records = [record for record in records if record.get("adapter") == adapter]
    return _json({"success": True, "artifacts": records[-limit:]})


def _handle_artifact_read(args: dict[str, Any], **_: Any) -> str:
    try:
        return _json({"success": True, "artifact": _load_artifact(str(args.get("artifact_id")))})
    except Exception as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _handle_artifact_extract(args: dict[str, Any], **_: Any) -> str:
    try:
        artifact = _load_artifact(str(args.get("artifact_id")))
        adapter = str(args.get("adapter") or "auto")
        if adapter != "auto" and adapter != artifact.get("adapter"):
            raise ValueError(f"requested adapter {adapter} does not match artifact adapter {artifact.get('adapter')}")
        evidence = _extract_artifact(artifact, str(args.get("detail") or "metadata"), str(args.get("intent") or "review"))
        return _json({"success": True, "artifact_id": artifact["artifact_id"], "evidence": evidence})
    except Exception as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _handle_artifact_normalize(args: dict[str, Any], **_: Any) -> str:
    try:
        artifact = _load_artifact(str(args.get("artifact_id")))
        semantic_schema = args.get("semantic_schema") if isinstance(args.get("semantic_schema"), dict) else {}
        hard_requirements = [str(item) for item in args.get("hard_requirements") or []]
        advisory_preferences = [str(item) for item in args.get("advisory_preferences") or []]
        provided_evidence_ids = [str(item) for item in args.get("evidence_ids") or []]
        normalized = _build_normalized_schema(
            artifact,
            schema_profile=str(args.get("schema_profile") or "auto"),
            intent=str(args.get("intent") or "fidelity"),
            semantic_schema=semantic_schema,
            hard_requirements=hard_requirements,
            advisory_preferences=advisory_preferences,
            evidence_ids=provided_evidence_ids,
        )
        reviewed_fields = bool(semantic_schema or hard_requirements or advisory_preferences)
        evidence = _store_evidence(
            artifact,
            extractor="artifact_normalize",
            claim_level="reviewed" if reviewed_fields else "verified",
            summary=f"Normalized {artifact.get('adapter', 'unknown')} artifact to {ARTIFACT_SCHEMA_VERSION}.",
            data=normalized,
        )
        return _json({"success": True, "artifact_id": artifact["artifact_id"], "schema_evidence": evidence})
    except Exception as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _build_normalized_schema(
    artifact: dict[str, Any],
    *,
    schema_profile: str,
    intent: str,
    semantic_schema: dict[str, Any],
    hard_requirements: list[str],
    advisory_preferences: list[str],
    evidence_ids: list[str],
) -> dict[str, Any]:
    adapter = str(artifact.get("adapter") or "binary")
    profile = schema_profile if schema_profile != "auto" else _default_schema_profile(adapter)
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "profile": profile,
        "intent": intent,
        "artifact": {
            "artifact_id": artifact["artifact_id"],
            "role": artifact.get("role"),
            "adapter": adapter,
            "detected_type": artifact.get("detected_type"),
            "path": artifact.get("path"),
            "sha256": artifact.get("sha256"),
            "size_bytes": artifact.get("size_bytes"),
            "authority": artifact.get("authority"),
            "created_at": artifact.get("created_at"),
        },
        "deterministic": _deterministic_schema_for_artifact(artifact),
        "semantic_schema": semantic_schema,
        "fidelity_contract": {
            "hard_requirements": hard_requirements,
            "advisory_preferences": advisory_preferences,
            "claim_boundary": _claim_boundary_for_adapter(adapter),
        },
        "transform_policy": _transform_policy_for_adapter(adapter),
        "evidence_basis": list(dict.fromkeys([*(artifact.get("evidence_ids") or []), *evidence_ids])),
    }


def _default_schema_profile(adapter: str) -> str:
    return {
        "image": "raster_image",
        "pdf": "pdf_document",
        "text": "text_document",
        "dxf": "cad_dxf",
        "docx": "word_document",
        "xlsx": "spreadsheet_workbook",
        "html": "html_document",
        "svg": "svg_vector_graphic",
        "step": "step_cad_model",
        "ifc": "ifc_bim_model",
        "zip": "zip_archive",
        "audio": "audio_media",
        "video": "video_media",
        "binary": "binary_file",
    }.get(adapter, "binary_file")


def _claim_boundary_for_adapter(adapter: str) -> str:
    if adapter == "image":
        return "Raster pixels can support visual/layout review; exact dimensions require supplied measurements or vector/CAD evidence."
    if adapter == "pdf":
        return "Text and page geometry are deterministic when extractable; scanned pages require OCR or vision review for content claims."
    if adapter == "dxf":
        return "DXF entities, layers, and units are deterministic geometry authority when parsed successfully."
    if adapter == "text":
        return "Line and character spans are deterministic; semantic summaries are reviewed unless separately validated."
    if adapter == "docx":
        return "DOCX package structure, paragraphs, tables, and extracted text are deterministic from OpenXML; visual pagination is application-dependent."
    if adapter == "xlsx":
        return "XLSX workbook sheets, dimensions, cells, formulas, and shared strings are deterministic from OpenXML; recalculated values require spreadsheet-engine validation."
    if adapter == "html":
        return "HTML tags, title, headings, links, images, and extracted text are deterministic; browser layout requires rendering validation."
    if adapter == "svg":
        return "SVG viewport, viewBox, elements, text, and vector primitives are deterministic from XML; raster appearance depends on renderer support."
    if adapter == "step":
        return "STEP entities, schema, and header metadata are deterministic from ISO-10303 text; engineering semantics require domain-specific validation."
    if adapter == "ifc":
        return "IFC entities, schema, spatial hierarchy counts, and property text are deterministic from STEP text; code compliance requires domain review."
    if adapter == "zip":
        return "ZIP entry names, CRCs, sizes, timestamps, and archive structure are deterministic; nested content claims require registering contained files."
    if adapter == "audio":
        return "Audio container/header metadata is deterministic; speech/music content requires transcription or signal analysis."
    if adapter == "video":
        return "Video container/header metadata is deterministic; scene/action content requires frame extraction or vision review."
    if adapter == "binary":
        return "Checksum, size, MIME guess, and byte signatures are deterministic; file-content claims require a structured adapter."
    return "Only file metadata and checksum are deterministic for this adapter."


def _transform_policy_for_adapter(adapter: str) -> dict[str, Any]:
    if adapter == "image":
        return {
            "preferred_routes": ["exact_copy", "render_schema", "artifact_transform.edit_image.openai_codex"],
            "forbidden_routes": ["prompt_only_image_generate_for_source_bound_work"],
        }
    if adapter == "dxf":
        return {
            "preferred_routes": ["exact_copy", "render_schema", "dxf_vector_transform"],
            "forbidden_routes": ["raster_only_dimension_claims"],
        }
    if adapter == "pdf":
        return {
            "preferred_routes": ["exact_copy", "extract_text_tables", "render_schema", "ocr_or_vision_for_scanned_pages"],
            "forbidden_routes": ["claiming_unextracted_text_or_tables"],
        }
    if adapter == "text":
        return {
            "preferred_routes": ["exact_copy", "line_span_transform", "render_schema"],
            "forbidden_routes": ["uncited_rewrite_when_fidelity_required"],
        }
    if adapter == "docx":
        return {
            "preferred_routes": ["exact_copy", "openxml_transform", "render_schema"],
            "forbidden_routes": ["flattened_text_rewrite_without_openxml_review"],
        }
    if adapter == "xlsx":
        return {
            "preferred_routes": ["exact_copy", "openxml_workbook_transform", "render_schema"],
            "forbidden_routes": ["manual_cell_claims_without_sheet_range_evidence"],
        }
    if adapter == "html":
        return {
            "preferred_routes": ["exact_copy", "dom_transform", "render_schema", "browser_render_review"],
            "forbidden_routes": ["claiming_layout_without_browser_render"],
        }
    if adapter == "svg":
        return {
            "preferred_routes": ["exact_copy", "xml_vector_transform", "render_schema"],
            "forbidden_routes": ["raster_only_vector_claims"],
        }
    if adapter == "step":
        return {
            "preferred_routes": ["exact_copy", "step_entity_transform", "render_schema"],
            "forbidden_routes": ["invented_cad_geometry_without_step_entities"],
        }
    if adapter == "ifc":
        return {
            "preferred_routes": ["exact_copy", "ifc_entity_transform", "render_schema"],
            "forbidden_routes": ["building_claims_without_ifc_entity_evidence"],
        }
    if adapter == "zip":
        return {
            "preferred_routes": ["exact_copy", "archive_manifest_transform", "register_nested_files", "render_schema"],
            "forbidden_routes": ["nested_content_claims_without_extraction"],
        }
    if adapter == "audio":
        return {
            "preferred_routes": ["exact_copy", "audio_metadata_transform", "transcription_then_review", "render_schema"],
            "forbidden_routes": ["speech_or_content_claims_without_transcription"],
        }
    if adapter == "video":
        return {
            "preferred_routes": ["exact_copy", "video_metadata_transform", "frame_extraction_then_review", "render_schema"],
            "forbidden_routes": ["scene_or_action_claims_without_frame_review"],
        }
    if adapter == "binary":
        return {
            "preferred_routes": ["exact_copy", "checksum_metadata_transform", "render_schema"],
            "forbidden_routes": ["content_claims_without_adapter"],
        }
    return {"preferred_routes": ["metadata_preserving_transform"], "forbidden_routes": ["unsupported_content_claims"]}


def _deterministic_schema_for_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    path = Path(artifact["path"])
    adapter = artifact.get("adapter")
    base = {
        "path": str(path),
        "sha256": artifact.get("sha256"),
        "detected_type": artifact.get("detected_type"),
        "size_bytes": path.stat().st_size if path.exists() else artifact.get("size_bytes"),
    }
    if adapter == "image":
        return {**base, **_image_metadata(path)}
    if adapter == "text":
        return {**base, **_text_metadata(path)}
    if adapter == "pdf":
        return {**base, **_pdf_metadata(path)}
    if adapter == "dxf":
        return {**base, **_dxf_metadata(path)}
    if adapter == "docx":
        return {**base, **_docx_metadata(path)}
    if adapter == "xlsx":
        return {**base, **_xlsx_metadata(path)}
    if adapter == "html":
        return {**base, **_html_metadata(path)}
    if adapter == "svg":
        return {**base, **_svg_metadata(path)}
    if adapter == "step":
        return {**base, **_step_metadata(path)}
    if adapter == "ifc":
        return {**base, **_ifc_metadata(path)}
    if adapter == "zip":
        return {**base, **_zip_metadata(path)}
    if adapter == "audio":
        return {**base, **_audio_metadata(path)}
    if adapter == "video":
        return {**base, **_video_metadata(path)}
    if adapter == "binary":
        return {**base, **_binary_metadata(path)}
    return {**base, "adapter": adapter or "binary"}


def _image_metadata(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        return {
            "adapter": "image",
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "has_alpha": image.mode in {"RGBA", "LA"} or "transparency" in image.info,
            "exif_orientation": image.getexif().get(274) if hasattr(image, "getexif") else None,
        }


def _text_metadata(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    encoding = "utf-8"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "utf-8-replace"
        text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return {
        "adapter": "text",
        "encoding": encoding,
        "line_count": len(lines),
        "char_count": len(text),
        "preview": text[:4000],
        "line_spans": [{"line": index + 1, "chars": len(line)} for index, line in enumerate(lines[:200])],
        "truncated_line_spans": len(lines) > 200,
    }


def _pdf_metadata(path: Path) -> dict[str, Any]:
    try:
        import pymupdf
    except Exception:
        import fitz as pymupdf

    doc = pymupdf.open(str(path))
    try:
        page_sizes = []
        text_chars = 0
        first_page_text = ""
        for page_index, page in enumerate(doc):
            rect = page.rect
            page_sizes.append({"page": page_index + 1, "width": rect.width, "height": rect.height})
            page_text = page.get_text() or ""
            text_chars += len(page_text)
            if page_index == 0:
                first_page_text = page_text[:4000]
        return {
            "adapter": "pdf",
            "page_count": doc.page_count,
            "page_sizes": page_sizes,
            "text_chars": text_chars,
            "first_page_text_preview": first_page_text,
            "scanned_or_image_only": text_chars == 0,
        }
    finally:
        doc.close()


def _dxf_metadata(path: Path) -> dict[str, Any]:
    import ezdxf

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    entity_counts = Counter(entity.dxftype() for entity in msp)
    extents: dict[str, Any] | None = None
    try:
        bbox = msp.bbox()
        if bbox.has_data:
            extents = {
                "min": [bbox.extmin.x, bbox.extmin.y, bbox.extmin.z],
                "max": [bbox.extmax.x, bbox.extmax.y, bbox.extmax.z],
            }
    except Exception:
        extents = None
    return {
        "adapter": "dxf",
        "dxfversion": doc.dxfversion,
        "insunits": doc.header.get("$INSUNITS", 0),
        "layers": [layer.dxf.name for layer in doc.layers],
        "blocks": [block.name for block in doc.blocks],
        "modelspace_entity_counts": dict(sorted(entity_counts.items())),
        "modelspace_extents": extents,
    }


def _xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xml_text(element: ET.Element) -> str:
    return "".join(text for text in element.itertext() if text)


def _zip_read_text(archive: zipfile.ZipFile, name: str) -> str:
    return archive.read(name).decode("utf-8", errors="replace")


def _docx_metadata(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            document_xml = _zip_read_text(archive, "word/document.xml") if "word/document.xml" in names else ""
    except Exception as exc:
        return {"adapter": "docx", "package_valid": False, "parse_error": f"{type(exc).__name__}: {exc}"}
    paragraphs: list[str] = []
    table_count = 0
    if document_xml:
        try:
            root = ET.fromstring(document_xml)
            for element in root.iter():
                name = _xml_name(element.tag)
                if name == "tbl":
                    table_count += 1
                elif name == "p":
                    text = _xml_text(element).strip()
                    if text:
                        paragraphs.append(text)
        except ET.ParseError as exc:
            return {
                "adapter": "docx",
                "package_valid": True,
                "xml_valid": False,
                "parse_error": f"document.xml: {exc}",
                "entry_count": len(names),
            }
    text = "\n".join(paragraphs)
    return {
        "adapter": "docx",
        "package_valid": True,
        "xml_valid": True,
        "entry_count": len(names),
        "paragraph_count": len(paragraphs),
        "table_count": table_count,
        "text_chars": len(text),
        "text_preview": text[:4000],
        "core_entries": sorted(name for name in names if name in {"[Content_Types].xml", "word/document.xml", "docProps/core.xml", "docProps/app.xml"}),
    }


def _xlsx_metadata(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            workbook_xml = _zip_read_text(archive, "xl/workbook.xml") if "xl/workbook.xml" in names else ""
            shared_xml = _zip_read_text(archive, "xl/sharedStrings.xml") if "xl/sharedStrings.xml" in names else ""
            worksheet_names = sorted(name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml"))
            worksheets = [_xlsx_sheet_summary(archive, name) for name in worksheet_names]
    except Exception as exc:
        return {"adapter": "xlsx", "package_valid": False, "parse_error": f"{type(exc).__name__}: {exc}"}
    sheet_names: list[str] = []
    if workbook_xml:
        try:
            root = ET.fromstring(workbook_xml)
            sheet_names = [str(element.attrib.get("name")) for element in root.iter() if _xml_name(element.tag) == "sheet" and element.attrib.get("name")]
        except ET.ParseError:
            sheet_names = []
    shared_strings = _xlsx_shared_strings(shared_xml)
    return {
        "adapter": "xlsx",
        "package_valid": True,
        "entry_count": len(names),
        "sheet_count": len(worksheet_names),
        "sheet_names": sheet_names,
        "worksheets": worksheets,
        "shared_string_count": len(shared_strings),
        "shared_string_preview": shared_strings[:20],
    }


def _xlsx_shared_strings(shared_xml: str) -> list[str]:
    if not shared_xml:
        return []
    try:
        root = ET.fromstring(shared_xml)
    except ET.ParseError:
        return []
    strings: list[str] = []
    for item in root.iter():
        if _xml_name(item.tag) == "si":
            value = _xml_text(item).strip()
            if value:
                strings.append(value)
    return strings


def _xlsx_sheet_summary(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(_zip_read_text(archive, name))
    except Exception as exc:
        return {"path": name, "parse_error": f"{type(exc).__name__}: {exc}"}
    dimension = None
    rows = set()
    cells = []
    formulas = 0
    for element in root.iter():
        local = _xml_name(element.tag)
        if local == "dimension":
            dimension = element.attrib.get("ref")
        elif local == "row" and element.attrib.get("r"):
            rows.add(element.attrib["r"])
        elif local == "c":
            ref = element.attrib.get("r")
            if ref:
                cells.append(ref)
        elif local == "f":
            formulas += 1
    return {
        "path": name,
        "dimension": dimension,
        "row_count": len(rows),
        "cell_count": len(cells),
        "formula_count": formulas,
        "cell_refs_preview": cells[:50],
    }


class _HTMLSummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tag_counts: Counter[str] = Counter()
        self.links: list[str] = []
        self.images: list[str] = []
        self.headings: list[dict[str, str]] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag_counts[tag.lower()] += 1
        attr_map = {key.lower(): value for key, value in attrs if value is not None}
        if tag.lower() == "a" and attr_map.get("href"):
            self.links.append(attr_map["href"])
        if tag.lower() == "img" and attr_map.get("src"):
            self.images.append(attr_map["src"])
        if tag.lower() in {"title", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._capture = tag.lower()

    def handle_endtag(self, tag: str) -> None:
        if self._capture == tag.lower():
            self._capture = None

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        self.text_parts.append(text)
        if self._capture == "title":
            self.title_parts.append(text)
        elif self._capture and self._capture.startswith("h"):
            self.headings.append({"level": self._capture, "text": text})


def _html_metadata(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    parser = _HTMLSummaryParser()
    parser.feed(text)
    body_text = " ".join(parser.text_parts)
    return {
        "adapter": "html",
        "encoding": "utf-8-replace" if b"\xef\xbf\xbd" in raw else "utf-8",
        "title": " ".join(parser.title_parts)[:500],
        "tag_counts": dict(sorted(parser.tag_counts.items())),
        "heading_count": len(parser.headings),
        "headings": parser.headings[:50],
        "link_count": len(parser.links),
        "image_count": len(parser.images),
        "links_preview": parser.links[:50],
        "images_preview": parser.images[:50],
        "text_chars": len(body_text),
        "text_preview": body_text[:4000],
    }


def _svg_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return {"adapter": "svg", "xml_valid": False, "parse_error": str(exc), "text_preview": text[:4000]}
    element_counts: Counter[str] = Counter(_xml_name(element.tag) for element in root.iter())
    text_items = [" ".join(_xml_text(element).split()) for element in root.iter() if _xml_name(element.tag) == "text"]
    text_items = [item for item in text_items if item]
    return {
        "adapter": "svg",
        "xml_valid": True,
        "root_tag": _xml_name(root.tag),
        "width": root.attrib.get("width"),
        "height": root.attrib.get("height"),
        "viewBox": root.attrib.get("viewBox"),
        "element_counts": dict(sorted(element_counts.items())),
        "text_items": text_items[:100],
    }


def _step_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    entity_counts = _step_entity_counts(text)
    return {
        "adapter": "step",
        "schema": _step_schema(text),
        "entity_count": sum(entity_counts.values()),
        "entity_counts": entity_counts,
        "header_preview": _step_header_preview(text),
    }


def _ifc_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    entity_counts = _step_entity_counts(text)
    hierarchy_keys = ["IFCPROJECT", "IFCSITE", "IFCBUILDING", "IFCBUILDINGSTOREY", "IFCSPACE", "IFCWALL", "IFCDOOR", "IFCWINDOW"]
    return {
        "adapter": "ifc",
        "schema": _step_schema(text),
        "entity_count": sum(entity_counts.values()),
        "entity_counts": entity_counts,
        "spatial_hierarchy_counts": {key: entity_counts.get(key, 0) for key in hierarchy_keys},
        "header_preview": _step_header_preview(text),
    }


def _step_entity_counts(text: str) -> dict[str, int]:
    counts = Counter(match.group(1).upper() for match in re.finditer(r"#\d+\s*=\s*([A-Z0-9_]+)\s*\(", text.upper()))
    return dict(sorted(counts.items()))


def _step_schema(text: str) -> str | None:
    match = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _step_header_preview(text: str) -> str:
    upper = text.upper()
    end = upper.find("ENDSEC;")
    return text[: end + len("ENDSEC;") if end != -1 else 1000]


def _zip_metadata(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            files = [entry for entry in entries if not entry.is_dir()]
            dirs = [entry for entry in entries if entry.is_dir()]
            preview = [
                {
                    "name": entry.filename,
                    "size": entry.file_size,
                    "compressed_size": entry.compress_size,
                    "crc": f"{entry.CRC:08x}",
                }
                for entry in files[:200]
            ]
    except Exception as exc:
        return {"adapter": "zip", "archive_valid": False, "parse_error": f"{type(exc).__name__}: {exc}"}
    return {
        "adapter": "zip",
        "archive_valid": True,
        "entry_count": len(entries),
        "file_count": len(files),
        "directory_count": len(dirs),
        "total_uncompressed_bytes": sum(entry.file_size for entry in files),
        "entries_preview": preview,
    }


def _audio_metadata(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".wav", ".wave"}:
        try:
            with wave.open(str(path), "rb") as audio:
                frames = audio.getnframes()
                rate = audio.getframerate()
                return {
                    "adapter": "audio",
                    "container": "wav",
                    "channels": audio.getnchannels(),
                    "sample_width_bytes": audio.getsampwidth(),
                    "sample_rate": rate,
                    "frame_count": frames,
                    "duration_seconds": round(frames / rate, 6) if rate else None,
                }
        except Exception as exc:
            return {"adapter": "audio", "container": "wav", "parse_error": f"{type(exc).__name__}: {exc}", **_byte_signature(path)}
    return {"adapter": "audio", "container": suffix.lstrip(".") or "unknown", **_byte_signature(path)}


def _video_metadata(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    data = _read_head(path, 1024 * 1024)
    boxes = _mp4_box_summary(data) if suffix in {".mp4", ".m4v", ".mov"} or b"ftyp" in data[:32] else []
    return {
        "adapter": "video",
        "container": suffix.lstrip(".") or "unknown",
        "box_counts": dict(sorted(Counter(box["type"] for box in boxes).items())),
        "boxes_preview": boxes[:50],
        **_byte_signature(path),
    }


def _binary_metadata(path: Path) -> dict[str, Any]:
    return {"adapter": "binary", **_byte_signature(path)}


def _byte_signature(path: Path) -> dict[str, Any]:
    head = _read_head(path, 64)
    return {
        "size_bytes": path.stat().st_size,
        "head_hex": head.hex(),
        "head_ascii": "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in head),
    }


def _mp4_box_summary(data: bytes) -> list[dict[str, Any]]:
    boxes: list[dict[str, Any]] = []
    offset = 0
    while offset + 8 <= len(data) and len(boxes) < 200:
        size, raw_type = struct.unpack(">I4s", data[offset : offset + 8])
        box_type = raw_type.decode("ascii", errors="replace")
        header = 8
        if size == 1 and offset + 16 <= len(data):
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header = 16
        elif size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            break
        boxes.append({"type": box_type, "offset": offset, "size": size})
        offset += size
    return boxes


def _extract_artifact(artifact: dict[str, Any], detail: str, intent: str) -> dict[str, Any]:
    path = Path(artifact["path"])
    adapter = artifact.get("adapter")
    if adapter == "image":
        return _extract_image(artifact, path, detail, intent)
    if adapter == "text":
        return _extract_text(artifact, path, detail, intent)
    if adapter == "pdf":
        return _extract_pdf(artifact, path, detail, intent)
    if adapter == "dxf":
        return _extract_dxf(artifact, path, detail, intent)
    if adapter == "docx":
        return _extract_structured(artifact, path, detail, intent, extractor="openxml_docx", metadata=_docx_metadata(path))
    if adapter == "xlsx":
        return _extract_structured(artifact, path, detail, intent, extractor="openxml_xlsx", metadata=_xlsx_metadata(path))
    if adapter == "html":
        return _extract_structured(artifact, path, detail, intent, extractor="html_parser", metadata=_html_metadata(path))
    if adapter == "svg":
        return _extract_structured(artifact, path, detail, intent, extractor="svg_xml_parser", metadata=_svg_metadata(path))
    if adapter == "step":
        return _extract_structured(artifact, path, detail, intent, extractor="step_text_parser", metadata=_step_metadata(path))
    if adapter == "ifc":
        return _extract_structured(artifact, path, detail, intent, extractor="ifc_step_parser", metadata=_ifc_metadata(path))
    if adapter == "zip":
        return _extract_structured(artifact, path, detail, intent, extractor="zip_manifest", metadata=_zip_metadata(path))
    if adapter == "audio":
        return _extract_structured(artifact, path, detail, intent, extractor="audio_header_parser", metadata=_audio_metadata(path))
    if adapter == "video":
        return _extract_structured(artifact, path, detail, intent, extractor="video_container_parser", metadata=_video_metadata(path))
    if adapter == "binary":
        return _extract_structured(artifact, path, detail, intent, extractor="binary_signature", metadata=_binary_metadata(path))
    return _store_evidence(
        artifact,
        extractor="file_metadata",
        claim_level="verified",
        summary="Basic file metadata extracted.",
        data={
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": artifact.get("sha256"),
            "detected_type": artifact.get("detected_type"),
            "detail": detail,
            "intent": intent,
        },
    )


def _extract_structured(
    artifact: dict[str, Any],
    path: Path,
    detail: str,
    intent: str,
    *,
    extractor: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    data = {**metadata, "detail": detail, "intent": intent}
    adapter = str(metadata.get("adapter") or artifact.get("adapter") or "file")
    summary_parts = [f"{adapter.upper()} extracted"]
    for key in (
        "paragraph_count",
        "sheet_count",
        "page_count",
        "entity_count",
        "file_count",
        "text_chars",
        "duration_seconds",
        "size_bytes",
    ):
        if key in metadata:
            summary_parts.append(f"{key}={metadata[key]}")
    return _store_evidence(
        artifact,
        extractor=extractor,
        claim_level="verified",
        summary=", ".join(summary_parts) + ".",
        data=data,
    )


def _extract_image(artifact: dict[str, Any], path: Path, detail: str, intent: str) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        data = {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "has_alpha": image.mode in {"RGBA", "LA"} or "transparency" in image.info,
            "exif_orientation": image.getexif().get(274) if hasattr(image, "getexif") else None,
            "detail": detail,
            "intent": intent,
        }
    return _store_evidence(
        artifact,
        extractor="pillow",
        claim_level="verified",
        summary=f"Image metadata extracted: {data['width']}x{data['height']} {data['format']}.",
        data=data,
    )


def _extract_text(artifact: dict[str, Any], path: Path, detail: str, intent: str) -> dict[str, Any]:
    raw = path.read_bytes()
    encoding = "utf-8"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "utf-8-replace"
        text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    data = {
        "encoding": encoding,
        "line_count": len(lines),
        "char_count": len(text),
        "preview": text[:4000],
        "detail": detail,
        "intent": intent,
    }
    return _store_evidence(
        artifact,
        extractor="text_adapter",
        claim_level="verified",
        summary=f"Text extracted: {len(lines)} lines, {len(text)} characters.",
        data=data,
    )


def _extract_pdf(artifact: dict[str, Any], path: Path, detail: str, intent: str) -> dict[str, Any]:
    try:
        import pymupdf
    except Exception:
        import fitz as pymupdf

    doc = pymupdf.open(str(path))
    try:
        page_sizes = []
        text_chars = 0
        first_page_text = ""
        table_counts = []
        for page_index, page in enumerate(doc):
            rect = page.rect
            page_sizes.append({"page": page_index + 1, "width": rect.width, "height": rect.height})
            page_text = page.get_text() or ""
            text_chars += len(page_text)
            if page_index == 0:
                first_page_text = page_text[:4000]
            if hasattr(page, "find_tables"):
                try:
                    tables = page.find_tables()
                    table_counts.append({"page": page_index + 1, "tables": len(tables.tables)})
                except Exception:
                    table_counts.append({"page": page_index + 1, "tables": None})
        data = {
            "page_count": doc.page_count,
            "page_sizes": page_sizes,
            "text_chars": text_chars,
            "first_page_text_preview": first_page_text,
            "table_counts": table_counts,
            "scanned_or_image_only": text_chars == 0,
            "detail": detail,
            "intent": intent,
        }
    finally:
        doc.close()
    return _store_evidence(
        artifact,
        extractor="pymupdf",
        claim_level="verified",
        summary=f"PDF extracted: {data['page_count']} pages, {data['text_chars']} text characters.",
        data=data,
    )


def _extract_dxf(artifact: dict[str, Any], path: Path, detail: str, intent: str) -> dict[str, Any]:
    import ezdxf

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    entity_counts = Counter(entity.dxftype() for entity in msp)
    data = {
        "dxfversion": doc.dxfversion,
        "insunits": doc.header.get("$INSUNITS", 0),
        "layers": [layer.dxf.name for layer in doc.layers],
        "blocks": [block.name for block in doc.blocks],
        "modelspace_entity_counts": dict(sorted(entity_counts.items())),
        "detail": detail,
        "intent": intent,
    }
    return _store_evidence(
        artifact,
        extractor="ezdxf",
        claim_level="verified",
        summary=f"DXF extracted: {len(data['layers'])} layers, {sum(entity_counts.values())} modelspace entities.",
        data=data,
    )


def _coerce_source_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        ids = [value]
    elif isinstance(value, list):
        ids = [str(item) for item in value]
    else:
        raise ValueError("source_artifact_ids must be a non-empty list")
    if not ids:
        raise ValueError("source_artifact_ids must be non-empty")
    if len(ids) > MAX_SOURCE_ARTIFACTS:
        raise ValueError(f"source_artifact_ids supports at most {MAX_SOURCE_ARTIFACTS} source artifacts")
    return ids


def _handle_artifact_transform(args: dict[str, Any], **_: Any) -> str:
    try:
        operation = str(args.get("operation") or "exact_copy")
        if operation not in {"edit_image", "render_schema", "exact_copy"}:
            raise ValueError(f"unsupported artifact_transform operation: {operation}")
        source_ids = _coerce_source_ids(args.get("source_artifact_ids"))
        source_artifacts = [_load_artifact(artifact_id) for artifact_id in source_ids]
        if operation == "exact_copy":
            result = _exact_copy_transform(
                source_artifacts=source_artifacts,
                contract_id=str(args.get("contract_id") or "manual"),
                instructions=str(args.get("instructions") or ""),
            )
            return _json({"success": True, **result})
        if operation == "render_schema":
            result = _render_schema_transform(
                source_artifacts=source_artifacts,
                contract_id=str(args.get("contract_id") or "manual"),
                instructions=str(args.get("instructions") or ""),
                output_type=str(args.get("output_type") or "image"),
                schema_evidence_id=str(args.get("schema_evidence_id") or ""),
                schema=args.get("schema") if isinstance(args.get("schema"), dict) else {},
                render_title=str(args.get("render_title") or "Botji artifact schema preview"),
            )
            return _json({"success": True, **result})
        prompt = str(args.get("instructions") or "").strip()
        if not prompt:
            raise ValueError("instructions are required for edit_image")
        for artifact in source_artifacts:
            if artifact.get("adapter") != "image":
                raise ValueError(f"edit_image requires image artifacts, got {artifact.get('artifact_id')} adapter={artifact.get('adapter')}")
        provider_route = _resolve_provider_route(str(args.get("provider_route") or "auto"))
        common = {
            "source_artifacts": source_artifacts,
            "prompt": prompt,
            "contract_id": str(args.get("contract_id") or "manual"),
            "quality": str(args.get("quality") or "high"),
            "size": str(args.get("size") or "auto"),
            "output_format": str(args.get("output_format") or "png"),
            "fidelity_mode": str(args.get("fidelity_mode") or "strict"),
        }
        result = _openai_codex_image_generate(**common)
        return _json({"success": True, **result})
    except Exception as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _exact_copy_transform(
    *,
    source_artifacts: list[dict[str, Any]],
    contract_id: str,
    instructions: str,
) -> dict[str, Any]:
    if len(source_artifacts) != 1:
        raise ValueError("exact_copy requires exactly one source artifact")
    source = source_artifacts[0]
    source_path = Path(source["path"])
    output_id = _new_id("art")
    output_dir = _artifact_root() / "outputs" / output_id
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix or ".bin"
    output_path = output_dir / f"exact-copy{suffix}"
    shutil.copy2(source_path, output_path)
    declared_type = str(source.get("declared_type") or source.get("adapter") or "auto")
    output_record = _create_output_artifact(
        output_id=output_id,
        output_path=output_path,
        declared_type=declared_type,
        parents=[source["artifact_id"]],
        user_intent=instructions or "Byte-exact source copy for 100% fidelity preservation.",
        extra={
            "contract_id": contract_id,
            "route": "artifact_transform.exact_copy",
            "exact_fidelity": True,
            "source_sha256": source.get("sha256"),
            "output_sha256": _sha256(output_path),
        },
    )
    route_evidence = _store_evidence(
        output_record,
        extractor="exact_copy",
        claim_level="verified",
        summary="Output artifact was created by deterministic byte-exact copy.",
        data={
            "route": "artifact_transform.exact_copy",
            "contract_id": contract_id,
            "source_artifact_id": source["artifact_id"],
            "source_path": source.get("path"),
            "output_artifact_id": output_id,
            "output_path": str(output_path),
            "source_sha256": source.get("sha256"),
            "output_sha256": _sha256(output_path),
            "sha256_match": source.get("sha256") == _sha256(output_path),
            "source_size_bytes": source.get("size_bytes"),
            "output_size_bytes": output_path.stat().st_size,
            "instructions": instructions,
        },
    )
    return {
        "output_artifact": _load_artifact(output_id),
        "route_evidence": route_evidence,
        "provider": "deterministic",
        "model": "byte-exact-copy",
        "endpoint": "artifact_transform.exact_copy",
    }


def _render_schema_transform(
    *,
    source_artifacts: list[dict[str, Any]],
    contract_id: str,
    instructions: str,
    output_type: str,
    schema_evidence_id: str,
    schema: dict[str, Any],
    render_title: str,
) -> dict[str, Any]:
    schema_payload, schema_basis = _resolve_schema_payload(source_artifacts, schema_evidence_id, schema)
    output_id = _new_id("art")
    output_dir = _artifact_root() / "outputs" / output_id
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_output_type = output_type if output_type in {"image", "json", "text"} else "image"
    if normalized_output_type == "json":
        output_path = output_dir / "schema.json"
        output_path.write_text(json.dumps(schema_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        declared_type = "text"
    elif normalized_output_type == "text":
        output_path = output_dir / "schema.md"
        output_path.write_text(_schema_to_markdown(schema_payload, render_title), encoding="utf-8")
        declared_type = "text"
    else:
        output_path = output_dir / "schema-preview.png"
        _render_schema_preview_png(schema_payload, output_path, render_title)
        declared_type = "image"

    output_record = _create_output_artifact(
        output_id=output_id,
        output_path=output_path,
        declared_type=declared_type,
        parents=[artifact["artifact_id"] for artifact in source_artifacts],
        user_intent=instructions or f"Deterministic schema render for {schema_payload.get('profile', 'artifact')}.",
        extra={
            "contract_id": contract_id,
            "route": "artifact_transform.render_schema",
            "schema_version": schema_payload.get("schema_version", ARTIFACT_SCHEMA_VERSION),
            "schema_profile": schema_payload.get("profile"),
            "schema_evidence_id": schema_evidence_id or schema_basis.get("evidence_id"),
            "output_type": normalized_output_type,
        },
    )
    route_evidence = _store_evidence(
        output_record,
        extractor="schema_render",
        claim_level="verified",
        summary="Output artifact was created by deterministic schema rendering.",
        data={
            "route": "artifact_transform.render_schema",
            "contract_id": contract_id,
            "source_artifact_ids": [artifact["artifact_id"] for artifact in source_artifacts],
            "schema_evidence_id": schema_evidence_id or schema_basis.get("evidence_id"),
            "schema_version": schema_payload.get("schema_version", ARTIFACT_SCHEMA_VERSION),
            "schema_profile": schema_payload.get("profile"),
            "output_type": normalized_output_type,
            "instructions": instructions,
        },
    )
    return {
        "output_artifact": _load_artifact(output_id),
        "route_evidence": route_evidence,
        "provider": "deterministic",
        "model": "schema-renderer",
        "endpoint": "artifact_transform.render_schema",
    }


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
            "STRICT SOURCE FIDELITY REQUIRED. You are performing a source-bound artifact transformation. "
            "MANDATORY RULES — violating any rule makes the output unusable:\n"
            "1. DO NOT add any object, furniture, appliance, plant, decoration, or clutter not visible in the source image.\n"
            "2. DO NOT remove any element present in the source.\n"
            "3. Preserve the exact spatial layout, left-to-right module order, and object positions.\n"
            "4. Preserve object counts exactly — no extra chairs, no extra appliances, no extra anything.\n"
            "5. The source image is the authoritative reference. Every element in your output must be traceable to the source.\n"
            f"Fidelity mode: {fidelity_mode}. Transform instructions: {prompt}"
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


def _handle_artifact_review(args: dict[str, Any], **_: Any) -> str:
    try:
        contract_id = str(args.get("contract_id") or "manual")
        source_ids = _coerce_source_ids(args.get("source_artifact_ids"))
        output_id = str(args.get("output_artifact_id") or "")
        if not output_id:
            raise ValueError("output_artifact_id is required")
        sources = [_load_artifact(artifact_id) for artifact_id in source_ids]
        output = _load_artifact(output_id)
        use_openai_vision = bool(args.get("use_openai_vision", True))
        require_vision_api = bool(args.get("require_vision_api", False))
        review_provider_route = str(args.get("review_provider_route") or "auto")
        provided_evidence_ids = [str(item) for item in args.get("evidence_ids") or []]
        fidelity_requirements = [str(item) for item in args.get("fidelity_requirements") or []]
        review = _build_review(
            contract_id=contract_id,
            sources=sources,
            output=output,
            provided_evidence_ids=provided_evidence_ids,
            fidelity_requirements=fidelity_requirements,
            use_openai_vision=use_openai_vision,
            require_vision_api=require_vision_api,
            review_provider_route=review_provider_route,
        )
        review_path = _reviews_dir() / f"{review['review_id']}.json"
        _write_json(review_path, review)
        artifact_review_path = _artifact_root() / "reviews" / f"{review['review_id']}.json"
        _write_json(artifact_review_path, review)
        return _json({"success": True, "review": review, "review_path": str(review_path), "artifact_review_path": str(artifact_review_path)})
    except Exception as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _reviews_dir() -> Path:
    return _hermes_home() / "reviews"


def _axis(axis: str, status: str, severity: str, notes: str, *, claim_level: str = "reviewed", evidence_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "axis": axis,
        "compare_status": status,
        "severity": severity,
        "notes": notes,
        "claim_level": claim_level,
        "evidence_ids": evidence_ids or [],
    }


def _geometry_fidelity_note(output: dict[str, Any]) -> str:
    adapter = str(output.get("adapter") or "unknown")
    route = str(output.get("route") or "")
    if route == "artifact_transform.exact_copy":
        return "Geometry/layout/content are byte-identical to the source artifact; no generative drift is possible in this route."
    if route == "artifact_transform.render_schema":
        return "Geometry/layout is deterministic for the normalized schema preview; source-to-schema extraction may still be reviewed unless parser-derived."
    if adapter == "dxf":
        return "DXF geometry is deterministic when parsed from entities, layers, units, and extents."
    if adapter == "pdf":
        return "PDF page geometry is deterministic; scanned visual content still requires OCR or vision review."
    if adapter == "text":
        return "Text line and character spans are deterministic; semantic meaning is reviewed unless validated separately."
    if adapter == "image":
        return "Raster image geometry review is visual/reviewed, not a deterministic physical measurement."
    return "Only file metadata geometry is available for this adapter."


COMPARATOR_KEYS = {
    "image": ("width", "height", "mode", "format", "has_alpha"),
    "text": ("encoding", "line_count", "char_count", "line_spans", "truncated_line_spans"),
    "pdf": ("page_count", "page_sizes", "text_chars", "scanned_or_image_only"),
    "dxf": ("dxfversion", "insunits", "layers", "modelspace_entity_counts", "modelspace_extents"),
    "docx": ("package_valid", "xml_valid", "paragraph_count", "table_count", "text_chars"),
    "xlsx": ("package_valid", "sheet_count", "sheet_names", "worksheets", "shared_string_count"),
    "html": ("title", "tag_counts", "heading_count", "link_count", "image_count", "text_chars"),
    "svg": ("xml_valid", "root_tag", "width", "height", "viewBox", "element_counts", "text_items"),
    "step": ("schema", "entity_count", "entity_counts"),
    "ifc": ("schema", "entity_count", "entity_counts", "spatial_hierarchy_counts"),
    "zip": ("archive_valid", "entry_count", "file_count", "directory_count", "total_uncompressed_bytes"),
    "audio": ("container", "channels", "sample_rate", "frame_count", "duration_seconds", "head_hex"),
    "video": ("container", "box_counts", "head_hex", "size_bytes"),
    "binary": ("sha256", "size_bytes", "head_hex"),
}


def _run_modality_comparators(
    *,
    sources: list[dict[str, Any]],
    output: dict[str, Any],
    provided_evidence_ids: list[str],
    fidelity_requirements: list[str],
) -> dict[str, Any]:
    schema_payloads = _review_schema_payloads(sources, output, provided_evidence_ids)
    comparisons = []
    blockers: list[str] = []
    warnings: list[str] = []
    for source in sources:
        adapter = str(source.get("adapter") or "binary")
        current = _deterministic_schema_for_artifact(source)
        schema_payload = schema_payloads.get(source["artifact_id"])
        if not schema_payload:
            blockers.append(f"No normalized schema evidence is available for source {source['artifact_id']} adapter={adapter}.")
            comparisons.append({
                "source_artifact_id": source["artifact_id"],
                "adapter": adapter,
                "status": "conflict",
                "comparator": _comparator_name(adapter),
                "failures": ["missing_normalized_schema"],
                "warnings": [],
            })
            continue
        expected = schema_payload.get("deterministic") if isinstance(schema_payload.get("deterministic"), dict) else {}
        comparison = _compare_deterministic_metadata(adapter, current, expected)
        comparison["source_artifact_id"] = source["artifact_id"]
        comparisons.append(comparison)
        if comparison["status"] == "conflict":
            blockers.extend(f"{source['artifact_id']}: {item}" for item in comparison.get("failures", []))
        elif comparison["status"] == "partial":
            warnings.extend(f"{source['artifact_id']}: {item}" for item in comparison.get("warnings", []))

    status = "conflict" if blockers else ("partial" if warnings else "match")
    summary = (
        "Modality comparators matched normalized schema evidence for all source artifacts."
        if status == "match"
        else "Modality comparators found schema/content drift." if status == "conflict"
        else "Modality comparators matched core fields with non-blocking warnings."
    )
    evidence = _store_evidence(
        output,
        extractor="artifact_modality_comparator",
        claim_level="verified" if status == "match" else "reviewed",
        summary=summary,
        data={
            "status": status,
            "comparisons": comparisons,
            "fidelity_requirements": fidelity_requirements,
            "output_route": output.get("route"),
        },
    )
    return {
        "status": status,
        "summary": summary,
        "evidence_id": evidence["evidence_id"],
        "blockers": blockers,
        "warnings": warnings,
        "comparisons": comparisons,
    }


def _run_exact_output_compare(sources: list[dict[str, Any]], output: dict[str, Any]) -> dict[str, Any] | None:
    if str(output.get("route") or "") != "artifact_transform.exact_copy":
        return None
    blockers: list[str] = []
    if len(sources) != 1:
        blockers.append("exact_copy review requires exactly one source artifact")
        source = sources[0] if sources else {}
    else:
        source = sources[0]
    source_sha = source.get("sha256")
    output_sha = output.get("sha256")
    source_size = source.get("size_bytes")
    output_size = output.get("size_bytes")
    if source_sha != output_sha:
        blockers.append(f"source/output sha256 mismatch: {source_sha} != {output_sha}")
    if source_size != output_size:
        blockers.append(f"source/output size mismatch: {source_size} != {output_size}")
    status = "conflict" if blockers else "match"
    summary = (
        "Output is byte-identical to the source artifact; 100% file fidelity is verified."
        if status == "match"
        else "Output is not byte-identical to the source artifact."
    )
    evidence = _store_evidence(
        output,
        extractor="exact_output_compare",
        claim_level="verified" if status == "match" else "reviewed",
        summary=summary,
        data={
            "status": status,
            "source_artifact_id": source.get("artifact_id"),
            "output_artifact_id": output.get("artifact_id"),
            "source_sha256": source_sha,
            "output_sha256": output_sha,
            "source_size_bytes": source_size,
            "output_size_bytes": output_size,
            "blockers": blockers,
        },
    )
    return {
        "status": status,
        "summary": summary,
        "evidence_id": evidence["evidence_id"],
        "blockers": blockers,
    }


def _run_high_fidelity_provider_transform(
    sources: list[dict[str, Any]],
    output: dict[str, Any],
    provided_evidence_ids: list[str],
) -> dict[str, Any] | None:
    route = str(output.get("route") or "")
    if not route.startswith("artifact_transform.edit_image"):
        return None
    if output.get("adapter") != "image" or not all(source.get("adapter") == "image" for source in sources):
        return None

    blockers: list[str] = []
    warnings: list[str] = []
    source_ids = [source["artifact_id"] for source in sources]
    source_sha256s = [source.get("sha256") for source in sources]
    route_evidence = None
    route_evidence_id = ""
    for evidence_id in dict.fromkeys([*provided_evidence_ids, *(output.get("evidence_ids") or [])]):
        try:
            candidate = _load_evidence(str(evidence_id))
        except Exception:
            continue
        data = candidate.get("data") if isinstance(candidate.get("data"), dict) else {}
        if candidate.get("extractor") == "artifact_transform" and data.get("source_artifact_ids") == source_ids:
            route_evidence = data
            route_evidence_id = str(evidence_id)
            break

    provider = str(output.get("provider") or "")
    model = str(output.get("model") or "")
    quality = str(output.get("quality") or "")
    endpoint = str(output.get("endpoint") or "")
    if provider != "openai-codex":
        blockers.append(f"provider route is not Codex-backed: {provider or 'unknown'}")
    if model != "gpt-image-2":
        blockers.append(f"high-fidelity provider transform requires gpt-image-2, got {model or 'unknown'}")
    if quality != "high":
        blockers.append(f"100% transformation gate requires quality=high, got {quality or 'unknown'}")
    if not endpoint:
        blockers.append("provider endpoint was not recorded")
    if route_evidence is None:
        blockers.append("provider transform route evidence is missing")
        source_input_mode = ""
    else:
        source_input_mode = str(route_evidence.get("source_input_mode") or "")
        if source_input_mode not in {"input_image", "image[]"}:
            blockers.append(f"source images were not recorded as image inputs: {source_input_mode or 'missing'}")
        if route_evidence.get("source_sha256s") != source_sha256s:
            blockers.append("route evidence source SHA-256 list does not match registered source artifacts")
        if route_evidence.get("output_sha256") != output.get("sha256"):
            blockers.append("route evidence output SHA-256 does not match output artifact")
        if route_evidence.get("no_prompt_only_fallback") is not True:
            blockers.append("route evidence does not prove no prompt-only fallback")
        if model == "gpt-image-2" and route_evidence.get("input_fidelity_omitted") is not True:
            blockers.append("route evidence must record input_fidelity omitted for gpt-image-2 automatic high-fidelity inputs")

    status = "conflict" if blockers else ("partial" if warnings else "match")
    summary = (
        "GPT Image 2 source-image transform used high-fidelity image inputs with no prompt-only fallback."
        if status == "match"
        else "High-fidelity provider transform proof has non-blocking warnings."
        if status == "partial"
        else "High-fidelity provider transform proof is incomplete or inconsistent."
    )
    evidence = _store_evidence(
        output,
        extractor="high_fidelity_provider_transform",
        claim_level="reviewed",
        summary=summary,
        data={
            "status": status,
            "provider": provider,
            "model": model,
            "quality": quality,
            "endpoint": endpoint,
            "route": route,
            "route_evidence_id": route_evidence_id,
            "source_artifact_ids": source_ids,
            "source_sha256s": source_sha256s,
            "source_input_mode": source_input_mode,
            "output_artifact_id": output.get("artifact_id"),
            "output_sha256": output.get("sha256"),
            "input_fidelity_omitted": model == "gpt-image-2",
            "input_fidelity_rationale": "gpt-image-2 processes image inputs at high fidelity automatically; input_fidelity must be omitted.",
            "no_prompt_only_fallback": route_evidence.get("no_prompt_only_fallback") if route_evidence else False,
            "blockers": blockers,
            "warnings": warnings,
        },
    )
    return {
        "status": status,
        "summary": summary,
        "evidence_id": evidence["evidence_id"],
        "blockers": blockers,
        "warnings": warnings,
    }


def _review_schema_payloads(
    sources: list[dict[str, Any]],
    output: dict[str, Any],
    provided_evidence_ids: list[str],
) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    candidate_ids = list(provided_evidence_ids)
    if output.get("schema_evidence_id"):
        candidate_ids.append(str(output["schema_evidence_id"]))
    for evidence_id in dict.fromkeys(candidate_ids):
        try:
            evidence = _load_evidence(evidence_id)
        except Exception:
            continue
        data = evidence.get("data")
        if not isinstance(data, dict) or data.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            continue
        artifact = data.get("artifact") if isinstance(data.get("artifact"), dict) else {}
        artifact_id = str(artifact.get("artifact_id") or "")
        if artifact_id:
            payloads[artifact_id] = data
    if len(sources) == 1 and not payloads:
        try:
            payload, _ = _resolve_schema_payload(sources, str(output.get("schema_evidence_id") or ""), {})
            artifact = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else {}
            artifact_id = str(artifact.get("artifact_id") or sources[0]["artifact_id"])
            payloads[artifact_id] = payload
        except Exception:
            pass
    return payloads


def _compare_deterministic_metadata(adapter: str, current: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    normalized_adapter = adapter if adapter in COMPARATOR_KEYS else "binary"
    keys = COMPARATOR_KEYS[normalized_adapter]
    failures: list[str] = []
    warnings: list[str] = []
    checked = 0
    for key in keys:
        if key not in expected:
            continue
        if key not in current:
            failures.append(f"current extraction missing comparator field `{key}`")
            continue
        checked += 1
        if _canonical(current.get(key)) != _canonical(expected.get(key)):
            failures.append(f"field `{key}` changed from schema value {expected.get(key)!r} to current value {current.get(key)!r}")
    if checked == 0:
        warnings.append("no adapter-specific comparator fields were present in normalized schema")
    status = "conflict" if failures else ("partial" if warnings else "match")
    return {
        "adapter": adapter,
        "comparator": _comparator_name(normalized_adapter),
        "status": status,
        "checked_fields": list(keys),
        "failures": failures,
        "warnings": warnings,
    }


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _comparator_name(adapter: str) -> str:
    return {
        "image": "image_schema_metadata_compare",
        "text": "text_line_span_compare",
        "pdf": "pdf_page_text_compare",
        "dxf": "dxf_entity_geometry_compare",
        "docx": "docx_openxml_compare",
        "xlsx": "xlsx_openxml_compare",
        "html": "html_dom_text_compare",
        "svg": "svg_vector_xml_compare",
        "step": "step_entity_compare",
        "ifc": "ifc_entity_hierarchy_compare",
        "zip": "zip_manifest_crc_compare",
        "audio": "audio_header_compare",
        "video": "video_container_compare",
        "binary": "binary_checksum_signature_compare",
    }.get(adapter, "binary_checksum_signature_compare")


def _build_review(
    *,
    contract_id: str,
    sources: list[dict[str, Any]],
    output: dict[str, Any],
    provided_evidence_ids: list[str],
    fidelity_requirements: list[str],
    use_openai_vision: bool,
    require_vision_api: bool,
    review_provider_route: str,
) -> dict[str, Any]:
    review_id = _new_id("review")
    source_ids = [artifact["artifact_id"] for artifact in sources]
    output_parents = set(output.get("parents") or [])
    missing_lineage = sorted(set(source_ids) - output_parents)
    blockers: list[str] = []
    corrections: list[str] = []
    axes: list[dict[str, Any]] = []
    all_evidence = list(dict.fromkeys([*provided_evidence_ids, *(output.get("evidence_ids") or [])]))

    if missing_lineage:
        blockers.append(f"Output artifact is missing lineage to sources: {', '.join(missing_lineage)}")
        corrections.append("Regenerate through artifact_transform with source_artifact_ids preserved.")

    route = str(output.get("route") or "")
    route_ok = route.startswith("artifact_transform.")
    route_or_lineage_blocked = bool(missing_lineage or not route_ok)
    if not route_ok:
        blockers.append("Output route is not a source-aware artifact route.")
        corrections.append("Use artifact_transform instead of prompt-only generation.")

    vision_evidence_id = None
    vision_note = "No modality-specific comparator was requested; registry, lineage, route, and provided evidence were reviewed."
    vision_failed = False
    vision_warning = False
    vision_blockers: list[str] = []
    vision_corrections: list[str] = []
    if route != "artifact_transform.exact_copy" and use_openai_vision and output.get("adapter") == "image" and all(source.get("adapter") == "image" for source in sources):
        try:
            resolved_review_route = _resolve_review_provider_route(review_provider_route, output)
            if resolved_review_route != "openai_codex":
                raise RuntimeError(f"Unsupported review provider route: {resolved_review_route}")
            vision_payload = _codex_vision_compare(sources, output, fidelity_requirements)
            evidence = _store_evidence(
                output,
                extractor=f"{vision_payload['provider']}_vision_compare",
                claim_level="reviewed",
                summary="Codex vision comparison reviewed source and output images for visible fidelity.",
                data=vision_payload,
            )
            vision_evidence_id = evidence["evidence_id"]
            all_evidence.append(vision_evidence_id)
            assessment = _assess_vision_payload(vision_payload)
            vision_note = assessment["summary"]
            if assessment["verdict"] == "block":
                vision_blockers.extend(assessment["blockers"])
                vision_corrections.extend(assessment["corrections"])
                blockers.extend(assessment["blockers"])
                corrections.extend(assessment["corrections"])
            elif assessment["verdict"] == "warn":
                vision_warning = True
        except Exception as exc:
            vision_failed = True
            vision_note = f"Codex vision review failed: {type(exc).__name__}: {exc}"
            if require_vision_api:
                blockers.append(vision_note)
                corrections.append("Fix Codex vision review before accepting the artifact.")

    modality_result = _run_modality_comparators(
        sources=sources,
        output=output,
        provided_evidence_ids=provided_evidence_ids,
        fidelity_requirements=fidelity_requirements,
    )
    all_evidence.append(modality_result["evidence_id"])
    modality_note = modality_result["summary"]
    modality_warning = modality_result["status"] == "partial"
    if modality_result["blockers"]:
        blockers.extend(modality_result["blockers"])
        corrections.append("Regenerate from current normalized schema evidence or rerun artifact_normalize before review.")

    exact_result = _run_exact_output_compare(sources, output)
    exact_note = ""
    if exact_result:
        all_evidence.append(exact_result["evidence_id"])
        exact_note = exact_result["summary"]
        if exact_result["blockers"]:
            blockers.extend(exact_result["blockers"])
            corrections.append("Regenerate exact_copy output from the source artifact before claiming 100% fidelity.")

    high_fidelity_result = _run_high_fidelity_provider_transform(sources, output, provided_evidence_ids)
    if high_fidelity_result:
        all_evidence.append(high_fidelity_result["evidence_id"])
        if high_fidelity_result["blockers"]:
            blockers.extend(high_fidelity_result["blockers"])
            corrections.append("Use gpt-image-2 quality=high with source images as image inputs and persisted route evidence before claiming 100% transformation.")

    common_status = "conflict" if blockers else "match"
    common_severity = "blocking" if blockers else "none"
    grounding_note = (
        "Output claims are grounded in registry lineage and persisted evidence."
        if not blockers
        else (
            "Grounding failed because lineage or route is invalid."
            if route_or_lineage_blocked
            else "Grounding failed because source-fidelity review found hard visual conflicts."
        )
    )
    review_warning = vision_failed or vision_warning or modality_warning
    preserve_note = exact_note or (vision_note if vision_evidence_id or vision_failed or vision_warning else modality_note)
    uncertainty_status = "conflict" if blockers else ("partial" if review_warning else "match")
    uncertainty_severity = "blocking" if blockers else ("low" if review_warning else "none")
    exact_match = bool(exact_result and exact_result["status"] == "match")
    transform_contract_status = "conflict" if blockers else ("partial" if review_warning else "match")
    transform_contract_percent = 0 if blockers else (95 if review_warning else 100)
    byte_exact_percent = 100 if exact_match else 0
    if exact_match:
        claim_type = "byte_exact_file_fidelity"
        preservation_target = "byte_exact_file_identity"
    elif high_fidelity_result:
        claim_type = "transform_contract_fidelity"
        preservation_target = "source_constraint_preservation"
    elif route == "artifact_transform.render_schema":
        claim_type = "transform_contract_fidelity"
        preservation_target = "deterministic_schema_equivalence"
    else:
        claim_type = "reviewed_visual_similarity"
        preservation_target = "visual_reference_similarity"
    transform_contract_note = (
        "All hard source requirements passed under the allowed transform contract; this is 100% transform-contract fidelity, not byte-exact file identity."
        if transform_contract_status == "match" and not exact_match
        else "Output is byte-identical to the source artifact; both byte-exact and transform-contract fidelity are 100%."
        if exact_match
        else "Transform contract has non-blocking review warnings; it is not a 100% transform-fidelity pass."
        if transform_contract_status == "partial"
        else "Transform contract has blocking source-fidelity conflicts."
    )
    geometry_status = "conflict" if vision_blockers or modality_result["status"] == "conflict" or (exact_result and exact_result["status"] == "conflict") else ("match" if exact_match or (route == "artifact_transform.render_schema" and modality_result["status"] == "match") else "partial")
    geometry_severity = "blocking" if geometry_status == "conflict" else ("none" if geometry_status == "match" else "low")
    deterministic_claim = "verified" if all_evidence else "reviewed"
    axes.extend([
        _axis("source_coverage", "match" if sources else "missing", "none" if sources else "blocking", "Source artifact IDs are present." if sources else "No source artifacts were supplied.", evidence_ids=all_evidence),
        _axis("authority_alignment", "match", "none", "User-supplied source artifacts outrank generated output assumptions.", evidence_ids=all_evidence),
        _axis("preserve_change", "partial" if review_warning and not blockers else common_status, "medium" if review_warning and not blockers else common_severity, preserve_note, evidence_ids=all_evidence),
        _axis("groundedness", common_status, common_severity, grounding_note, evidence_ids=all_evidence),
        _axis("uncertainty", uncertainty_status, uncertainty_severity, preserve_note, evidence_ids=all_evidence),
        _axis("safety", "match", "none", "Artifact paths were constrained to Botji safe roots; no credential paths were used.", claim_level=deterministic_claim, evidence_ids=all_evidence),
        _axis("artifact_lineage", "conflict" if missing_lineage else "match", "blocking" if missing_lineage else "none", "Output parents include all source artifacts." if not missing_lineage else "Output lineage is incomplete.", claim_level=deterministic_claim, evidence_ids=all_evidence),
        _axis("actionability", "match" if not blockers else "missing", "none" if not blockers else "blocking", "Review receipt and output artifact path are persisted." if not blockers else "Correction is required before the artifact is usable.", evidence_ids=all_evidence),
        _axis("adapter_route", "match" if route_ok else "conflict", "none" if route_ok else "blocking", "Source-aware artifact route was used." if route_ok else "Prompt-only, manually registered, or unknown transform route was used.", claim_level=deterministic_claim, evidence_ids=all_evidence),
        _axis("lineage_integrity", "match" if not missing_lineage else "conflict", "none" if not missing_lineage else "blocking", "Parent artifact IDs match the source list." if not missing_lineage else "Parent artifact IDs are missing.", claim_level=deterministic_claim, evidence_ids=all_evidence),
        _axis("modality_comparator", modality_result["status"], "blocking" if modality_result["status"] == "conflict" else ("low" if modality_warning else "none"), modality_note, claim_level="verified" if modality_result["status"] == "match" else "reviewed", evidence_ids=[modality_result["evidence_id"]]),
        *([_axis("high_fidelity_provider_transform", high_fidelity_result["status"], "blocking" if high_fidelity_result["status"] == "conflict" else ("low" if high_fidelity_result["status"] == "partial" else "none"), high_fidelity_result["summary"], claim_level="reviewed", evidence_ids=[high_fidelity_result["evidence_id"]])] if high_fidelity_result else []),
        *([_axis("metadata_fidelity", exact_result["status"], "blocking" if exact_result["status"] == "conflict" else "none", exact_note, claim_level="verified" if exact_result["status"] == "match" else "reviewed", evidence_ids=[exact_result["evidence_id"]])] if exact_result else []),
        _axis("transform_contract_fidelity", transform_contract_status, "blocking" if blockers else ("low" if review_warning else "none"), transform_contract_note, claim_level="verified" if exact_match else "reviewed", evidence_ids=all_evidence),
        _axis("content_fidelity", "conflict" if vision_blockers or modality_result["status"] == "conflict" or (exact_result and exact_result["status"] == "conflict") else ("partial" if review_warning and not blockers else "match"), "blocking" if vision_blockers or modality_result["status"] == "conflict" or (exact_result and exact_result["status"] == "conflict") else ("low" if review_warning and not blockers else "none"), preserve_note, evidence_ids=[exact_result["evidence_id"]] if exact_result else ([vision_evidence_id] if vision_evidence_id else [modality_result["evidence_id"]])),
        _axis("layout_fidelity", "conflict" if vision_blockers or modality_result["status"] == "conflict" or (exact_result and exact_result["status"] == "conflict") else ("partial" if review_warning and not blockers else "match"), "blocking" if vision_blockers or modality_result["status"] == "conflict" or (exact_result and exact_result["status"] == "conflict") else ("low" if review_warning and not blockers else "none"), preserve_note, evidence_ids=[exact_result["evidence_id"]] if exact_result else ([vision_evidence_id] if vision_evidence_id else [modality_result["evidence_id"]])),
        _axis("geometry_fidelity", geometry_status, geometry_severity, _geometry_fidelity_note(output), evidence_ids=all_evidence),
        _axis("unknowns_handling", "match", "none", "Exact physical dimensions are not claimed unless supplied by deterministic source evidence.", evidence_ids=all_evidence),
    ])
    verdict = "block" if blockers else ("warn" if review_warning else "pass")
    final_claim_level = "verified" if exact_match and not blockers else "reviewed"
    return {
        "review_id": review_id,
        "contract_id": contract_id,
        "reviewed_output_ref": output["artifact_id"],
        "verdict": verdict,
        "final_claim_level": final_claim_level,
        "axes": axes,
        "blockers": blockers,
        "required_corrections": corrections,
        "verification_steps": [
            "artifact_registry_lookup",
            "sha256_lineage_check",
            "artifact_transform_route_check",
            "artifact_modality_comparator",
            *(["exact_sha256_output_compare"] if exact_result else []),
            *(["high_fidelity_provider_transform"] if high_fidelity_result else []),
            *(["codex_vision_compare"] if vision_evidence_id else []),
        ],
        "reviewer_notes": preserve_note,
        "artifact_context": {
            "source_artifact_ids": source_ids,
            "output_artifact_id": output["artifact_id"],
            "evidence_ids": list(dict.fromkeys(all_evidence)),
        },
        "fidelity_scores": {
            "byte_exact_file_fidelity_percent": byte_exact_percent,
            "transform_contract_fidelity_percent": transform_contract_percent,
            "claim_type": claim_type,
            "preservation_target": preservation_target,
            "high_fidelity_provider_transform": high_fidelity_result["status"] if high_fidelity_result else "not_applicable",
            "claim_boundary": (
                "100% transform-contract fidelity means every hard source-preservation requirement passed under the explicit allowed-change list. "
                "For GPT Image 2 source-image edits, high-fidelity provider input handling is recorded separately and still does not mean byte-identical pixels or deterministic physical dimensions unless exact/schema evidence says so."
            ),
            "basis": [
                "source_artifact_lineage",
                "artifact_transform_route",
                "baseline_or_schema_evidence",
                *(("exact_sha256_output_compare",) if exact_result else ()),
                *(("high_fidelity_provider_transform",) if high_fidelity_result else ()),
                *(("codex_vision_compare",) if vision_evidence_id else ()),
                "artifact_modality_comparator",
            ],
        },
    }


def _data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _vision_review_prompt(fidelity_requirements: list[str]) -> str:
    if fidelity_requirements:
        requirements = "\n".join(f"- {item}" for item in fidelity_requirements)
    else:
        requirements = "- Preserve visible source layout, object identity, proportions, text/labels, and avoid invented elements."
    return (
        "Compare the source image(s) and output image for source fidelity using only visible evidence. "
        "Focus on object preservation, layout relationships, text/labels, geometry drift, and invented elements.\n\n"
        "Hard fidelity requirements:\n"
        f"{requirements}\n\n"
        "Return JSON only with this shape: "
        '{"verdict":"pass|warn|block","matches":[],"partials":[],"conflicts":[],"unknowns":[],"required_corrections":[]}. '
        "Use verdict=block when the output contradicts, omits, reorders, or invents anything that violates a hard requirement. "
        "Use verdict=warn for minor visible drift that does not violate a hard requirement. "
        "If a requirement starts with 'Allowed transform:', do not mark that permitted visual change as a partial or conflict by itself."
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(stripped[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _coerce_review_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False)]
    text = str(value).strip()
    return [text] if text else []


def _assess_vision_payload(vision_payload: dict[str, Any]) -> dict[str, Any]:
    text = str(vision_payload.get("comparison") or "").strip()
    data = _extract_json_object(text)
    if data is not None:
        raw_verdict = str(data.get("verdict") or "").strip().lower()
        conflicts = _coerce_review_items(data.get("conflicts") or data.get("blocking_conflicts"))
        partials = _coerce_review_items(data.get("partials"))
        unknowns = _coerce_review_items(data.get("unknowns"))
        matches = _coerce_review_items(data.get("matches"))
        corrections = _coerce_review_items(data.get("required_corrections") or data.get("corrections"))

        if raw_verdict in {"block", "fail", "failed", "conflict", "conflicted"} or conflicts:
            verdict = "block"
        elif raw_verdict in {"warn", "warning", "partial", "mixed", "uncertain"} or partials:
            verdict = "warn"
        else:
            verdict = "pass"

        blockers = conflicts if verdict == "block" else []
        if verdict == "block" and not blockers:
            blockers = [f"Vision review verdict is {raw_verdict or 'block'}."]
        if verdict == "block" and not corrections:
            corrections = ["Regenerate or revise the artifact until all hard fidelity conflicts are resolved."]

        summary = json.dumps(
            {
                "verdict": verdict,
                "matches": matches[:8],
                "partials": partials[:8],
                "conflicts": blockers[:8],
                "unknowns": unknowns[:8],
            },
            ensure_ascii=False,
        )
        return {
            "verdict": verdict,
            "blockers": blockers,
            "corrections": corrections,
            "summary": summary[:1600],
        }

    lowered = text.lower()
    block_signals = (
        '"verdict": "block"',
        '"verdict":"block"',
        '"verdict": "fail"',
        '"verdict":"fail"',
        "blocking conflict",
        "not faithful",
        "does not preserve",
        "wrong layout",
        "violates",
    )
    warn_signals = (
        '"verdict": "warn"',
        '"verdict":"warn"',
        "minor drift",
        "partial",
    )
    if any(signal in lowered for signal in block_signals):
        return {
            "verdict": "block",
            "blockers": [f"Vision review reported a fidelity conflict: {text[:400]}"],
            "corrections": ["Regenerate or revise the artifact until all hard fidelity conflicts are resolved."],
            "summary": text[:1600],
        }
    if any(signal in lowered for signal in warn_signals):
        return {"verdict": "warn", "blockers": [], "corrections": [], "summary": text[:1600]}
    return {"verdict": "pass", "blockers": [], "corrections": [], "summary": text[:1600] or "Vision review returned no text."}


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


def _codex_size_for_sources(size: str, source_artifacts: list[dict[str, Any]]) -> str:
    if isinstance(size, str) and size.strip().lower() != "auto":
        return size.strip()
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
        instructions=CODEX_IMAGE_INSTRUCTIONS,
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
        instructions=(
            "You are a strict artifact fidelity reviewer. Compare source and "
            "output images using only visible evidence. Return JSON only."
        ),
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


def register(ctx) -> None:
    ctx.register_tool(
        name="artifact_register",
        toolset="file",
        schema=ARTIFACT_REGISTER_SCHEMA,
        handler=_handle_artifact_register,
        description=ARTIFACT_REGISTER_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_extract",
        toolset="file",
        schema=ARTIFACT_EXTRACT_SCHEMA,
        handler=_handle_artifact_extract,
        description=ARTIFACT_EXTRACT_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_normalize",
        toolset="file",
        schema=ARTIFACT_NORMALIZE_SCHEMA,
        handler=_handle_artifact_normalize,
        description=ARTIFACT_NORMALIZE_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_transform",
        toolset="file",
        schema=ARTIFACT_TRANSFORM_SCHEMA,
        handler=_handle_artifact_transform,
        description=ARTIFACT_TRANSFORM_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_review",
        toolset="file",
        schema=ARTIFACT_REVIEW_SCHEMA,
        handler=_handle_artifact_review,
        description=ARTIFACT_REVIEW_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_list",
        toolset="file",
        schema=ARTIFACT_LIST_SCHEMA,
        handler=_handle_artifact_list,
        description=ARTIFACT_LIST_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_read",
        toolset="file",
        schema=ARTIFACT_READ_SCHEMA,
        handler=_handle_artifact_read,
        description=ARTIFACT_READ_SCHEMA["description"],
    )
