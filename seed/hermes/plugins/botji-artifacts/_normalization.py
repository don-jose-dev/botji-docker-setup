"""Exact-copy and schema-render transform operations."""
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
from _registry import _append_record, _store_evidence, _load_artifact
from _extraction import _build_normalized_schema
from _rendering import (
    _create_output_artifact, _render_schema_preview_png, _schema_to_markdown,
    _resolve_schema_payload, _load_evidence,
)


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
        # Guard: a degenerate preview (pure metadata text with tiny default font) produces
        # a ~12–15 KB PNG that vision reviewers correctly identify as a "text panel", not a
        # layout diagram. Fail fast here so the agent retries with richer schema data rather
        # than registering an unusable artifact.
        preview_size = output_path.stat().st_size
        _SCHEMA_PREVIEW_MIN_BYTES = 40_000
        if preview_size < _SCHEMA_PREVIEW_MIN_BYTES:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise ValueError(
                f"render_schema produced a degenerate preview ({preview_size:,} bytes < "
                f"{_SCHEMA_PREVIEW_MIN_BYTES:,} bytes). "
                "Populate semantic_schema.render_primitives with spatial layout blocks before "
                "calling render_schema, or use operation=edit_image for a provider-rendered output."
            )

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


