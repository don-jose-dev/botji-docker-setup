"""Hermes-native Botji core substrate.

This plugin intentionally does not interpret image quality, layouts, sketches,
or design intent. Skills own those semantic decisions. The plugin only records
durable source/output facts and enforces mechanical delivery rules:

- current-turn source lineage,
- parent existence,
- output registration,
- blocked receipt status,
- safe file storage.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _ok(**payload: Any) -> str:
    return _json({"success": True, **payload})


def _fail(message: str, **payload: Any) -> str:
    return _json({"success": False, "error": message, **payload})


def _new_id(prefix: str) -> str:
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()


def _core_root() -> Path:
    return Path(os.environ.get("BOTJI_CORE_ROOT", str(_hermes_home() / "botji-core"))).resolve()


def _safe_roots() -> list[Path]:
    raw_roots = [
        os.environ.get("HERMES_HOME", "/opt/data"),
        os.environ.get("BOTJI_ARTIFACT_ROOT", str(_hermes_home() / "artifacts")),
        os.environ.get("BOTJI_CORE_ROOT", str(_hermes_home() / "botji-core")),
        os.environ.get("BOTJI_WORKSPACE_ROOT", "/workspace"),
    ]
    roots: list[Path] = []
    for raw in raw_roots:
        try:
            roots.append(Path(raw).resolve())
        except OSError:
            continue
    return roots


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _looks_sensitive(path: Path) -> bool:
    lowered = [part.lower() for part in path.parts]
    sensitive_exact = {".env", "auth.json", "credentials.json", "id_rsa", "id_ed25519"}
    sensitive_fragments = ("secret", "token", "private_key")
    if any(part in sensitive_exact for part in lowered):
        return True
    return any(fragment in part for part in lowered for fragment in sensitive_fragments)


def _resolve_allowed_path(raw_path: str) -> Path:
    if not raw_path:
        raise ValueError("path is required")
    path = Path(raw_path).expanduser().resolve()
    if _looks_sensitive(path):
        raise PermissionError(f"refusing sensitive path: {path}")
    if not any(_is_relative_to(path, root) for root in _safe_roots()):
        roots = ", ".join(str(root) for root in _safe_roots())
        raise PermissionError(f"path must be under an allowed root ({roots}): {path}")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"file does not exist: {path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_path(kind: str) -> Path:
    return _core_root() / "index" / f"{kind}.jsonl"


def _append_record(kind: str, record: dict[str, Any]) -> None:
    path = _record_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _read_records(kind: str) -> list[dict[str, Any]]:
    path = _record_path(kind)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("botji-core: skipped invalid JSONL line in %s", path)
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _find_record(kind: str, record_id: str, id_field: str) -> dict[str, Any] | None:
    for record in reversed(_read_records(kind)):
        if str(record.get(id_field) or "") == str(record_id):
            return record
    return None


def _legacy_artifact_index_path() -> Path:
    root = Path(os.environ.get("BOTJI_ARTIFACT_ROOT", str(_hermes_home() / "artifacts"))).resolve()
    return root / "index" / "artifacts.jsonl"


def _find_legacy_artifact(artifact_id: str) -> dict[str, Any] | None:
    """Defense-in-depth lookup for the substrate hooks ONLY.

    V1R PR 1 (2026-05-23) removed the legacy fallback from core tool dispatch.
    Tools (`source_register`, `output_write`, `receipt_record`, `delivery_gate`)
    must NOT call this — they treat `art_*` IDs as non-existent. The defense
    hooks `hooks/stale_id_block.py` and `hooks/delivery_check.py` still call
    this for mechanical observation: detecting stale `art_*` references leaking
    out of the legacy plugin so they can be blocked / rewritten before delivery.

    Once V1R PR 11 deletes `botji-artifacts/`, the legacy index is gone and this
    function returns `None` for every input. The hooks fail open in that case.
    """
    path = _legacy_artifact_index_path()
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("botji-core: failed to read legacy artifact index %s: %s", path, exc)
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and str(record.get("artifact_id") or "") == str(artifact_id):
            compatible = dict(record)
            compatible["_record_source"] = "legacy-botji-artifacts"
            return compatible
    return None


def _find_native_artifact(artifact_id: str) -> dict[str, Any] | None:
    """Native-only artifact lookup for core tool dispatch.

    V1R PR 1 (2026-05-23): tools no longer transparently accept `art_*` IDs via
    legacy fallback. If a tool needs to look up an artifact, it gets a hit only
    if the artifact lives in the native `src_*` / `out_*` registry. Legacy
    `art_*` IDs return `None` and the caller raises `unknown source/output_id`
    — same error path as any other unrecognized ID.
    """
    return _find_record("artifacts", artifact_id, "artifact_id")


def _native_sources_for_turn(current_turn_id: str) -> list[str]:
    if not current_turn_id:
        return []
    matches: list[str] = []
    for record in _read_records("sources"):
        if str(record.get("current_turn_id") or "").strip() != current_turn_id:
            continue
        artifact_id = str(record.get("artifact_id") or "").strip()
        if artifact_id.startswith("src_"):
            matches.append(artifact_id)
    return matches


def _copy_into_store(src: Path, bucket: str, artifact_id: str) -> Path:
    dest_dir = _core_root() / bucket / artifact_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return dest


def _detect_mime(path: Path, declared_type: str | None = None) -> str:
    if declared_type:
        if "/" in declared_type:
            return declared_type
        if declared_type == "image":
            return mimetypes.guess_type(path.name)[0] or "image/unknown"
        if declared_type == "pdf":
            return "application/pdf"
        if declared_type in {"text", "txt"}:
            return "text/plain"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _required_text(args: dict[str, Any], key: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _list_arg(args: dict[str, Any], key: str) -> list[str]:
    value = args.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _matches_required_type(record: dict[str, Any], required_type: str | None) -> bool:
    if not required_type:
        return True
    wanted = required_type.lower().strip()
    declared = str(record.get("declared_type") or "").lower()
    mime = str(record.get("mime_type") or "").lower()
    return declared == wanted or mime.startswith(f"{wanted}/") or mime == wanted


def _handle_source_register(args: dict[str, Any], **_: Any) -> str:
    try:
        source_path = _resolve_allowed_path(_required_text(args, "path"))
        current_turn_id = _required_text(args, "current_turn_id")
        role = str(args.get("role") or "source").strip() or "source"
        declared_type = str(args.get("declared_type") or "").strip() or None
        source_id = _new_id("src")
        stored_path = _copy_into_store(source_path, "sources", source_id)
        stat = source_path.stat()
        record = {
            "artifact_id": source_id,
            "kind": "source",
            "role": role,
            "current_turn_id": current_turn_id,
            "declared_type": declared_type,
            "mime_type": _detect_mime(source_path, declared_type),
            "original_path": str(source_path),
            "path": str(stored_path),
            "sha256": _sha256(source_path),
            "size_bytes": stat.st_size,
            "created_at": _now(),
        }
        _append_record("sources", record)
        _append_record("artifacts", record)
        return _ok(source=record, source_id=source_id, artifact_id=source_id)
    except Exception as exc:
        logger.exception("botji-core: source_register failed")
        return _fail(str(exc))


def _handle_source_current(args: dict[str, Any], **_: Any) -> str:
    try:
        current_turn_id = _required_text(args, "current_turn_id")
        required_type = str(args.get("required_type") or "").strip() or None
        all_sources = _read_records("sources")
        sources = [
            record
            for record in all_sources
            if str(record.get("current_turn_id") or "") == current_turn_id
            and _matches_required_type(record, required_type)
        ]
        sources.sort(key=lambda record: str(record.get("created_at") or ""))
        return _ok(
            current_turn_id=current_turn_id,
            required_type=required_type,
            sources=sources,
            source_ids=[record.get("artifact_id") for record in sources],
        )
    except Exception as exc:
        logger.exception("botji-core: source_current failed")
        return _fail(str(exc))


def _handle_artifact_write(args: dict[str, Any], **_: Any) -> str:
    try:
        output_path = _resolve_allowed_path(_required_text(args, "path"))
        parents = _list_arg(args, "parents")
        if not parents:
            raise ValueError("parents must include at least one source artifact id")
        missing = [parent for parent in parents if _find_native_artifact(parent) is None]
        if missing:
            raise ValueError(f"unknown parent artifact ids: {', '.join(missing)}")
        artifact_id = _new_id("out")
        stored_path = _copy_into_store(output_path, "outputs", artifact_id)
        stat = output_path.stat()
        metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
        record = {
            "artifact_id": artifact_id,
            "kind": str(args.get("kind") or "output").strip() or "output",
            "claim_level": str(args.get("claim_level") or "draft").strip() or "draft",
            "current_turn_id": str(args.get("current_turn_id") or "").strip() or None,
            "parents": parents,
            "metadata": metadata,
            "mime_type": _detect_mime(output_path, str(args.get("declared_type") or "").strip() or None),
            "original_path": str(output_path),
            "path": str(stored_path),
            "sha256": _sha256(output_path),
            "size_bytes": stat.st_size,
            "created_at": _now(),
        }
        _append_record("artifacts", record)
        return _ok(artifact=record, output_id=artifact_id, artifact_id=artifact_id)
    except Exception as exc:
        logger.exception("botji-core: artifact_write failed")
        return _fail(str(exc))


def _handle_receipt_record(args: dict[str, Any], **_: Any) -> str:
    try:
        source_ids = _list_arg(args, "source_ids")
        if not source_ids:
            raise ValueError("source_ids must include at least one current source id")
        output_id = _required_text(args, "output_id")
        route = _required_text(args, "route")
        status = str(args.get("status") or "").strip().lower()
        if status not in {"pass", "warn", "block"}:
            raise ValueError("status must be one of: pass, warn, block")
        missing_sources = [source_id for source_id in source_ids if _find_native_artifact(source_id) is None]
        if missing_sources:
            raise ValueError(f"unknown source ids: {', '.join(missing_sources)}")
        if _find_native_artifact(output_id) is None:
            raise ValueError(f"unknown output_id: {output_id}")
        checks = args.get("checks") if isinstance(args.get("checks"), list) else []
        receipt_id = _new_id("rcpt")
        receipt = {
            "receipt_id": receipt_id,
            "source_ids": source_ids,
            "output_id": output_id,
            "route": route,
            "status": status,
            "checks": checks,
            "claim_level": str(args.get("claim_level") or "reviewed").strip() or "reviewed",
            "current_turn_id": str(args.get("current_turn_id") or "").strip() or None,
            "primary_blocker": args.get("primary_blocker"),
            "user_visible_summary": args.get("user_visible_summary"),
            "created_at": _now(),
        }
        _append_record("receipts", receipt)
        return _ok(receipt=receipt, receipt_id=receipt_id)
    except Exception as exc:
        logger.exception("botji-core: receipt_record failed")
        return _fail(str(exc))


def _block(receipt: dict[str, Any] | None, blocker: str, detail: str | None = None) -> str:
    return _ok(
        delivery_gate="blocked",
        recommended_action="retry_or_ask",
        primary_blocker=blocker,
        detail=detail,
        receipt=receipt,
    )


def _handle_delivery_gate(args: dict[str, Any], **_: Any) -> str:
    try:
        receipt_id = _required_text(args, "receipt_id")
        receipt = _find_record("receipts", receipt_id, "receipt_id")
        if receipt is None:
            return _block(None, "missing_receipt", f"receipt not found: {receipt_id}")
        status = str(receipt.get("status") or "").lower()
        if status == "block":
            return _block(receipt, str(receipt.get("primary_blocker") or "receipt_blocked"))
        if status not in {"pass", "warn"}:
            return _block(receipt, "invalid_receipt_status", status)

        source_ids = [str(item) for item in (receipt.get("source_ids") or []) if str(item).strip()]
        if not source_ids:
            return _block(receipt, "missing_sources", "receipt has no source_ids")
        sources = [_find_native_artifact(source_id) for source_id in source_ids]
        missing_sources = [source_id for source_id, source in zip(source_ids, sources) if source is None]
        if missing_sources:
            return _block(receipt, "missing_source_artifact", ", ".join(missing_sources))

        output_id = str(receipt.get("output_id") or "").strip()
        output = _find_native_artifact(output_id)
        if output is None:
            return _block(receipt, "missing_output_artifact", output_id)
        output_path = str(output.get("path") or "")
        if not output_path or not Path(output_path).exists():
            return _block(receipt, "missing_output_file", output_path or output_id)

        output_parents = {str(parent) for parent in (output.get("parents") or [])}
        missing_parent_links = [source_id for source_id in source_ids if source_id not in output_parents]
        if missing_parent_links:
            return _block(receipt, "output_parent_mismatch", ", ".join(missing_parent_links))

        current_turn_id = str(receipt.get("current_turn_id") or "").strip()
        if current_turn_id:
            stale_sources = [
                str(source.get("artifact_id"))
                for source in sources
                if source
                and str(source.get("current_turn_id") or "").strip()
                and str(source.get("current_turn_id") or "").strip() != current_turn_id
            ]
            if stale_sources:
                return _block(receipt, "stale_current_turn_source", ", ".join(stale_sources))

            # If the current turn already registered native botji-core sources
            # (src_*), reject any receipt whose source_ids point to legacy
            # art_* records. Same-bytes SHA fallback in the artifact guard
            # otherwise silently passes stale art_ IDs from earlier turns.
            native_for_turn = _native_sources_for_turn(current_turn_id)
            if native_for_turn:
                legacy_in_receipt = [
                    source_id
                    for source_id, source in zip(source_ids, sources)
                    if source
                    and str(source.get("_record_source") or "") == "legacy-botji-artifacts"
                ]
                if legacy_in_receipt:
                    return _block(
                        receipt,
                        "mixed_pipeline_source",
                        f"legacy art_ ids {', '.join(legacy_in_receipt)} present while current turn has native src_ ids {', '.join(native_for_turn)}",
                    )

        gate = "warned" if status == "warn" else "clear"
        return _ok(
            delivery_gate=gate,
            recommended_action="deliver",
            primary_blocker=None,
            receipt=receipt,
            output=output,
        )
    except Exception as exc:
        logger.exception("botji-core: delivery_gate failed")
        return _fail(str(exc), delivery_gate="blocked", primary_blocker="gate_error")


SOURCE_REGISTER_SCHEMA = {
    "name": "source_register",
    "description": "Register a current-turn source file with checksum, MIME, original path, and durable Botji source ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Current user attachment path."},
            "current_turn_id": {"type": "string", "description": "Stable ID for the current user request/turn."},
            "role": {"type": "string", "description": "Source role, usually source/reference/sketch."},
            "declared_type": {"type": "string", "description": "Optional type hint such as image, pdf, text."},
        },
        "required": ["path", "current_turn_id"],
    },
}

SOURCE_CURRENT_SCHEMA = {
    "name": "source_current",
    "description": "Return registered source IDs for the current turn, optionally filtered by type.",
    "parameters": {
        "type": "object",
        "properties": {
            "current_turn_id": {"type": "string"},
            "required_type": {"type": "string", "description": "Optional type filter such as image, pdf, text."},
        },
        "required": ["current_turn_id"],
    },
}

ARTIFACT_WRITE_SCHEMA = {
    "name": "artifact_write",
    "description": "Register a generated output artifact and link it to existing parent source IDs.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "parents": {"type": "array", "items": {"type": "string"}},
            "kind": {"type": "string"},
            "claim_level": {"type": "string", "enum": ["draft", "reviewed", "verified", "unverified"]},
            "current_turn_id": {"type": "string"},
            "declared_type": {"type": "string"},
            "metadata": {"type": "object"},
        },
        "required": ["path", "parents"],
    },
}

OUTPUT_WRITE_SCHEMA = {**ARTIFACT_WRITE_SCHEMA, "name": "output_write"}

RECEIPT_RECORD_SCHEMA = {
    "name": "receipt_record",
    "description": "Persist a skill-authored review receipt for a source-bound output.",
    "parameters": {
        "type": "object",
        "properties": {
            "source_ids": {"type": "array", "items": {"type": "string"}},
            "output_id": {"type": "string"},
            "route": {"type": "string"},
            "status": {"type": "string", "enum": ["pass", "warn", "block"]},
            "checks": {"type": "array"},
            "claim_level": {"type": "string", "enum": ["draft", "reviewed", "verified", "unverified"]},
            "current_turn_id": {"type": "string"},
            "primary_blocker": {"type": "string"},
            "user_visible_summary": {"type": "string"},
        },
        "required": ["source_ids", "output_id", "route", "status"],
    },
}

DELIVERY_GATE_SCHEMA = {
    "name": "delivery_gate",
    "description": "Apply mechanical delivery checks to a Botji receipt before user delivery.",
    "parameters": {
        "type": "object",
        "properties": {
            "receipt_id": {"type": "string"},
        },
        "required": ["receipt_id"],
    },
}


def register(ctx) -> None:
    ctx.register_tool(
        name="source_register",
        toolset="file",
        schema=SOURCE_REGISTER_SCHEMA,
        handler=_handle_source_register,
        description=SOURCE_REGISTER_SCHEMA["description"],
    )
    ctx.register_tool(
        name="source_current",
        toolset="file",
        schema=SOURCE_CURRENT_SCHEMA,
        handler=_handle_source_current,
        description=SOURCE_CURRENT_SCHEMA["description"],
    )
    ctx.register_tool(
        name="output_write",
        toolset="file",
        schema=OUTPUT_WRITE_SCHEMA,
        handler=_handle_artifact_write,
        description=ARTIFACT_WRITE_SCHEMA["description"],
    )
    # DELETED_BY: PR_11
    ctx.register_tool(
        name="artifact_write",
        toolset="file",
        schema=ARTIFACT_WRITE_SCHEMA,
        handler=_handle_artifact_write,
        description=ARTIFACT_WRITE_SCHEMA["description"],
    )
    ctx.register_tool(
        name="receipt_record",
        toolset="file",
        schema=RECEIPT_RECORD_SCHEMA,
        handler=_handle_receipt_record,
        description=RECEIPT_RECORD_SCHEMA["description"],
    )
    ctx.register_tool(
        name="delivery_gate",
        toolset="file",
        schema=DELIVERY_GATE_SCHEMA,
        handler=_handle_delivery_gate,
        description=DELIVERY_GATE_SCHEMA["description"],
    )
    # Cross-cutting hooks — see hooks/stale_id_block.py + hooks/delivery_check.py.
    if hasattr(ctx, "register_hook"):
        from .hooks import pre_tool_call as _pre_tool_call, transform_llm_output as _xform
        ctx.register_hook("pre_tool_call", _pre_tool_call)
        ctx.register_hook("transform_llm_output", _xform)
    _core_root().mkdir(parents=True, exist_ok=True)
    logger.info("botji-core: registered Hermes-native source/artifact/receipt tools (root=%s)", _core_root())

    # Mechanical observability — see docs/OBSERVABILITY.md. Fail-open: if the
    # exporter or hook registration trips, the plugin still serves tools.
    try:
        from .metrics import register_hooks, start_exporter
        if start_exporter():
            register_hooks(ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("botji-core: metrics init skipped (%s) — tools still serve", exc)
