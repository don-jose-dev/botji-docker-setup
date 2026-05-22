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


def _dedup_record_matches(
    record: dict[str, Any], sha256: str, role: str, current_turn_id: str
) -> bool:
    """Predicate: does this index record qualify as a dedup hit for the given context?

    A hit requires sha256 + role + current_turn_id to all match, and the stored
    file on disk to still exist. Cross-turn matches are intentionally rejected:
    same bytes uploaded in a different turn deserve a fresh art_* with their own
    user_intent and parents.
    """
    if record.get("sha256") != sha256:
        return False
    if record.get("role") != role:
        return False
    if str(record.get("current_turn_id") or "") != current_turn_id:
        return False
    stored = record.get("path")
    if not stored:
        return False
    try:
        return Path(stored).is_file()
    except (OSError, ValueError):
        return False


def _find_dedup_match(
    sha256: str, role: str, current_turn_id: str = ""
) -> dict[str, Any] | None:
    """Scan the artifacts index for a record matching (sha256, role, current_turn_id).

    Empty ``current_turn_id`` disables dedup entirely so legacy callers that have
    not been updated degrade to no-dedup rather than colliding across turns.
    Skips malformed lines and entries whose stored file is missing on disk.
    """
    if not current_turn_id:
        return None
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
            if _dedup_record_matches(record, sha256, role, current_turn_id):
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


def _build_artifact_record(
    *,
    artifact_id: str,
    role: str,
    stored_path: Path,
    original_path: str,
    original_filename: str,
    detected_type: str,
    declared_type: str,
    adapter: str,
    sha256: str,
    authority: str,
    parents: list[str] | None,
    user_intent: str,
    current_turn_id: str,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the dict written to the artifacts index for a new registration."""
    record = {
        "artifact_id": artifact_id,
        "role": role,
        "path": str(stored_path),
        "original_path": original_path,
        "original_filename": original_filename,
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
        "current_turn_id": current_turn_id,
        "risk_flags": [],
    }
    if extra:
        record.update(extra)
    return record


def _copy_into_bucket(path: Path, role: str, artifact_id: str) -> Path:
    """Copy a registered file into its owned bucket under the artifact root."""
    bucket = "outputs" if role == "output" else "sources"
    target_dir = _artifact_root() / bucket / artifact_id
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_path = target_dir / _safe_filename(path.name)
    shutil.copy2(path, stored_path)
    return stored_path


def _register_path(
    path: Path,
    *,
    role: str,
    authority: str,
    declared_type: str = "auto",
    copy_into_registry: bool = True,
    parents: list[str] | None = None,
    user_intent: str = "",
    current_turn_id: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    max_bytes = _max_bytes()
    size_bytes = path.stat().st_size
    if size_bytes > max_bytes:
        raise ValueError(f"artifact exceeds BOTJI_ARTIFACT_MAX_BYTES ({size_bytes} > {max_bytes})")

    # Hash the source up front so dedup can short-circuit before allocating
    # an artifact_id or copying bytes.
    sha256 = _sha256(path)

    existing = _find_dedup_match(sha256, role, current_turn_id)
    if existing is not None:
        return {**existing, "deduplicated": True, "dedup_match_id": existing.get("artifact_id")}

    artifact_id = _new_id("art")
    detected_type, adapter = _detect_type(path, declared_type)
    stored_path = path
    if copy_into_registry and role in {"source", "reference", "output"}:
        stored_path = _copy_into_bucket(path, role, artifact_id)

    record = _build_artifact_record(
        artifact_id=artifact_id,
        role=role,
        stored_path=stored_path,
        original_path=str(path),
        original_filename=path.name,
        detected_type=detected_type,
        declared_type=declared_type,
        adapter=adapter,
        sha256=sha256,
        authority=authority,
        parents=parents,
        user_intent=user_intent,
        current_turn_id=current_turn_id,
        extra=extra,
    )
    _append_record(record)
    return record


