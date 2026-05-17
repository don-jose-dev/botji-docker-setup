"""Output artifact creation, schema payload resolution, evidence loading, and schema-to-markdown.

PIL-based PNG preview rendering was extracted to ``_schema_preview.py`` — this file
is now focused on artifact lifecycle + schema document concerns. Re-exports
``_render_schema_preview_png`` so existing callers (``_normalization.py``) keep working.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _constants import ARTIFACT_SCHEMA_VERSION
from _utils import _now, _sha256, _artifact_root
from _detect import _detect_type
from _registry import _append_record
from _schema_preview import _render_schema_preview_png  # re-exported


def _create_output_artifact(
    *,
    output_id: str,
    output_path: Path,
    declared_type: str,
    parents: list[str],
    user_intent: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    """Build and persist the output artifact record. ``extra`` is merged after the base fields."""
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


def _load_evidence(evidence_id: str) -> dict[str, Any]:
    """Locate an evidence record by id under ``<artifact_root>/evidence/*/<id>.json``."""
    if not evidence_id:
        raise ValueError("evidence_id is required")
    evidence_root = _artifact_root() / "evidence"
    for path in evidence_root.glob(f"*/{evidence_id}.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid evidence JSON: {path}") from exc
    raise ValueError(f"evidence not found: {evidence_id}")


def _resolve_schema_payload(
    source_artifacts: list[dict[str, Any]],
    schema_evidence_id: str,
    schema: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(schema_payload, provenance_record)`` from one of three sources, in priority order:

    1. ``schema`` dict provided inline — wrapped to schema_version if missing.
    2. ``schema_evidence_id`` pointing at a prior normalize evidence record.
    3. Auto-normalize the first source artifact when neither of the above is available.
    """
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
    # Lazy import: _extraction imports _metadata which is a sibling — keep this here
    # so import-time module resolution does not pull the whole extraction stack into
    # callers that only need _create_output_artifact or _schema_to_markdown.
    from _extraction import _build_normalized_schema
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


def _schema_to_markdown(schema_payload: dict[str, Any], title: str) -> str:
    """Render a ``botji.artifact_schema.v1`` payload as a readable markdown document."""
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


__all__ = [
    "_create_output_artifact",
    "_resolve_schema_payload",
    "_load_evidence",
    "_schema_to_markdown",
    "_render_schema_preview_png",  # re-export for backward compat with _normalization
]
