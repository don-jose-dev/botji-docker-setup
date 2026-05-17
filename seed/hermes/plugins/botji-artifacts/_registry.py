"""Artifact registry I/O — read, write, store evidence, register paths."""
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
from _constants import ARTIFACT_SCHEMA_VERSION, STRUCTURED_ADAPTERS
from _utils import (
    _json, _now, _new_id, _artifact_root, _index_path, _safe_roots,
    _resolve_allowed_path, _safe_filename, _sha256, _max_bytes,
)
from _detect import _detect_type


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


