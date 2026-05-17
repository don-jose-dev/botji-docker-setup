"""Tool handler functions — thin dispatchers that call into submodules."""
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
from _utils import _json, _resolve_allowed_path, _artifact_root
from _registry import (
    _load_records, _load_artifact, _register_path, _store_evidence, _write_json,
)
from _extraction import (
    _extract_artifact, _build_normalized_schema, _coerce_source_ids,
)
from _normalization import _exact_copy_transform, _render_schema_transform
from _codex import _openai_codex_image_generate, _resolve_provider_route
from _review import _build_review, _reviews_dir
from _vision import _coerce_review_items


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
        # Build structured brief from explicit fields if provided, else use freeform instructions
        camera   = str(args.get("camera_brief") or "").strip()
        light    = str(args.get("light_brief") or "").strip()
        mood     = str(args.get("mood_brief") or "").strip()
        subject  = [str(s).strip() for s in (args.get("subject_inventory") or []) if str(s).strip()]
        preserve = [str(s).strip() for s in (args.get("hard_preserve") or []) if str(s).strip()]
        forbidden = [str(s).strip() for s in (args.get("forbidden_elements") or []) if str(s).strip()]
        raw_instructions = str(args.get("instructions") or "").strip()

        if any([camera, light, mood, subject, preserve, forbidden]):
            parts: list[str] = []
            if camera:    parts.append(f"CAMERA: {camera}")
            if light:     parts.append(f"LIGHT: {light}")
            if mood:      parts.append(f"MOOD: {mood}")
            if subject:   parts.append("SUBJECT:\n" + "\n".join(f"  - {s}" for s in subject))
            if preserve:  parts.append("HARD PRESERVE:\n" + "\n".join(f"  - {s}" for s in preserve))
            if forbidden: parts.append("FORBIDDEN:\n" + "\n".join(f"  - {s}" for s in forbidden))
            structured = "\n".join(parts)
            prompt = (structured + "\n" + raw_instructions).strip() if raw_instructions else structured
        else:
            prompt = raw_instructions

        if not prompt:
            raise ValueError("instructions (or structured brief fields) are required for edit_image")
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

        # Promote the delivery gate to a prominent top-level field on the tool
        # response so the agent sees it first. The bare review object nests this
        # information deep, which made earlier turns ship blocked outputs anyway.
        verdict = review.get("verdict", "unknown")
        delivery_gate = review.get("delivery_gate", "unknown")
        recommended_action = review.get("recommended_action", "unknown")
        primary_blocker = review.get("primary_blocker")
        retry_guidance = review.get("retry_guidance")

        if delivery_gate == "blocked":
            notice = (
                "[DELIVERY GATE: BLOCKED] Do not deliver this artifact. "
                "Retry with corrections or surface the blocker text to the user."
            )
        elif delivery_gate == "warned":
            notice = "[DELIVERY GATE: WARNED] Deliverable, but mention review caveats to the user."
        else:
            notice = "[DELIVERY GATE: CLEAR] Artifact passed source-fidelity review."

        # Verdict file is written by the transform_tool_result hook in
        # botji-artifacts/__init__.py — plugin tool handlers don't receive
        # session_id (registry.dispatch only forwards task_id+user_task),
        # but the post-tool hook does. Keeping _write_verdict_file in this
        # module as the shared writer; the hook is the call site.

        return _json({
            "success": True,
            "delivery_gate_notice": notice,
            "verdict": verdict,
            "delivery_gate": delivery_gate,
            "recommended_action": recommended_action,
            "primary_blocker": primary_blocker,
            "retry_guidance": retry_guidance,
            "review": review,
            "review_path": str(review_path),
            "artifact_review_path": str(artifact_review_path),
        })
    except Exception as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _write_verdict_file(
    *,
    session_id: str,
    verdict: str,
    delivery_gate: str,
    recommended_action: str,
    primary_blocker: Any,
    retry_guidance: Any,
    review: dict[str, Any],
) -> None:
    """Persist the per-axis verdict the botji-gate hook reads.

    Format matches what botji-gate expects: top-level delivery_gate string
    plus an ``axes`` dict mapping axis name -> True/False. The hook reads
    the file by session_id and blocks delivery when any axis is False.
    """
    verdict_root = Path(os.environ.get("BOTJI_VERDICT_ROOT", "/opt/data/verdicts"))
    verdict_root.mkdir(parents=True, exist_ok=True)
    axes_map = {
        ax.get("axis"): (ax.get("severity") in (None, "none", "low") and ax.get("compare_status") != "conflict")
        for ax in (review.get("axes") or [])
        if ax.get("axis")
    }
    payload = {
        "verdict": verdict,
        "delivery_gate": delivery_gate,
        "recommended_action": recommended_action,
        "primary_blocker": primary_blocker,
        "retry_guidance": retry_guidance,
        "axes": axes_map,
        "review_id": review.get("review_id"),
        "reviewed_output_ref": review.get("reviewed_output_ref"),
    }
    out_path = verdict_root / f"{session_id}.verdict.json"
    tmp_path = out_path.with_suffix(".verdict.json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(out_path)  # atomic rename so the gate never reads a partial file

