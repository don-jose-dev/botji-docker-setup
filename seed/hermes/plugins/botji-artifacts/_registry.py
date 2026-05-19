"""Artifact registry I/O — read, write, store evidence, register paths."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from _utils import (
    _now, _new_id, _artifact_root, _index_path, _safe_filename, _sha256, _max_bytes,
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


def _find_dedup_match(sha256: str, role: str) -> dict[str, Any] | None:
    """Scan the artifacts index line-by-line for a record matching (sha256, role).

    Short-circuits on first match (newest entries near the bottom are scanned last;
    we accept any match since sha256 equality is what we care about). Skips
    malformed lines (corrupted manifests) and entries whose stored file is missing
    on disk (orphan index entries). Returns the matched record or None.
    """
    path = _index_path()
    if not path.is_file():
        return None
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return None
    try:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("sha256") != sha256:
                continue
            if record.get("role") != role:
                continue
            stored = record.get("path")
            if not stored:
                continue
            try:
                if not Path(stored).is_file():
                    continue
            except (OSError, ValueError):
                continue
            return record
    finally:
        handle.close()
    return None


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

    # Hash the source up front so we can content-address dedup before allocating
    # an artifact_id or copying bytes into the registry. The hash of the source
    # equals the hash of the copy (shutil.copy2 preserves content), so checking
    # here is equivalent to checking after the copy — and saves the copy on hit.
    sha256 = _sha256(path)

    # Dedup: if an existing artifact has the same (sha256, role) and its stored
    # file is still on disk, return it verbatim instead of registering a duplicate.
    existing = _find_dedup_match(sha256, role)
    if existing is not None:
        return {**existing, "deduplicated": True, "dedup_match_id": existing.get("artifact_id")}

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
        "sha256": sha256,
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


