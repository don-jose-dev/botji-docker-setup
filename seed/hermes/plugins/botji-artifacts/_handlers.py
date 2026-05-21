"""Tool handler functions — thin dispatchers that call into submodules."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from _constants import ARTIFACT_SCHEMA_VERSION
from _models import (
    ArtifactExtractManifestParams,
    ArtifactExtractParams,
    ArtifactListParams,
    ArtifactNormalizeParams,
    ArtifactReadParams,
    ArtifactRegisterParams,
    ArtifactReviewParams,
    ArtifactTransformParams,
)
from _utils import _json, _resolve_allowed_path, _artifact_root
from _registry import (
    _load_records, _load_artifact, _register_path, _store_evidence, _write_json,
)
from _extraction import (
    _extract_artifact, _build_normalized_schema,
)
from _normalization import _exact_copy_transform, _render_schema_transform
from _codex import _openai_codex_image_generate, _resolve_provider_route, _codex_extract_manifest
from _review import _build_review, _reviews_dir


_HERMES_NATIVE_ID_PREFIXES = ("src_", "out_", "rcpt_")


def _reject_hermes_native_ids(ids: list[str], tool_name: str) -> None:
    bad = [str(item) for item in ids if str(item).startswith(_HERMES_NATIVE_ID_PREFIXES)]
    if not bad:
        return
    shown = ", ".join(bad[:3])
    if len(bad) > 3:
        shown += f", ... ({len(bad)} total)"
    raise ValueError(
        f"{tool_name} expects legacy botji-artifacts art_* IDs, but received Hermes-native ID(s): {shown}. "
        "Do not pass src_*/out_*/rcpt_* IDs to legacy artifact_* tools. Use source_current, "
        "artifact_write, receipt_record, and delivery_gate for Hermes-native flow; or call "
        "artifact_register on the current attachment path and use the returned art_* ID for legacy artifact_* tools."
    )


def _handle_artifact_register(args: dict[str, Any], **_: Any) -> str:
    try:
        params = ArtifactRegisterParams.model_validate(args)
        source = _resolve_allowed_path(params.path, field="path")
        extra: dict[str, Any] | None = {"route": params.route} if params.route else None
        record = _register_path(
            source,
            role=params.role,
            authority=params.authority,
            declared_type=params.declared_type,
            copy_into_registry=params.copy_into_registry,
            parents=params.parents,
            user_intent=params.user_intent,
            extra=extra,
        )
        # Lift dedup metadata from the record dict to the top level of the response
        # so callers can branch on `deduplicated` without unpacking the artifact.
        if record.pop("deduplicated", False):
            dedup_match_id = record.pop("dedup_match_id", None)
            return _json({
                "success": True,
                "artifact": record,
                "deduplicated": True,
                "dedup_match_id": dedup_match_id,
            })
        return _json({"success": True, "artifact": record})
    except (ValidationError, Exception) as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _handle_artifact_list(args: dict[str, Any], **_: Any) -> str:
    params = ArtifactListParams.model_validate(args)
    records = _load_records()
    if params.role:
        records = [r for r in records if r.get("role") == params.role]
    if params.adapter:
        records = [r for r in records if r.get("adapter") == params.adapter]
    return _json({"success": True, "artifacts": records[-params.limit:]})


def _handle_artifact_read(args: dict[str, Any], **_: Any) -> str:
    try:
        params = ArtifactReadParams.model_validate(args)
        _reject_hermes_native_ids([params.artifact_id], "artifact_read")
        return _json({"success": True, "artifact": _load_artifact(params.artifact_id)})
    except (ValidationError, Exception) as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _handle_artifact_extract(args: dict[str, Any], **_: Any) -> str:
    try:
        params = ArtifactExtractParams.model_validate(args)
        _reject_hermes_native_ids([params.artifact_id], "artifact_extract")
        artifact = _load_artifact(params.artifact_id)
        if params.adapter != "auto" and params.adapter != artifact.get("adapter"):
            raise ValueError(
                f"requested adapter {params.adapter} does not match "
                f"artifact adapter {artifact.get('adapter')}"
            )
        evidence = _extract_artifact(artifact, params.detail, params.intent)
        return _json({"success": True, "artifact_id": artifact["artifact_id"], "evidence": evidence})
    except (ValidationError, Exception) as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _handle_artifact_extract_manifest(args: dict[str, Any], **_: Any) -> str:
    """Extract a structured spatial manifest from an image artifact using vision.

    Returns a manifest with scene_type, source_modality, elements[], element_count,
    adjacency_constraints[], layout_hints[], and fidelity_requirements[] that are
    ready to pass directly to artifact_review.
    """
    try:
        params = ArtifactExtractManifestParams.model_validate(args)
        _reject_hermes_native_ids([params.artifact_id], "artifact_extract_manifest")
        artifact = _load_artifact(params.artifact_id)
        if artifact.get("adapter") != "image":
            raise ValueError(
                f"artifact_extract_manifest requires an image artifact, "
                f"got adapter={artifact.get('adapter')}"
            )
        result = _codex_extract_manifest(artifact)
        manifest = result.get("manifest") or {}
        evidence = _store_evidence(
            artifact,
            extractor="codex_manifest_extractor",
            claim_level="reviewed",
            summary=(
                f"Spatial manifest extracted: scene={manifest.get('scene_type', 'unknown')}, "
                f"modality={manifest.get('source_modality', 'unknown')}, "
                f"elements={manifest.get('element_count', '?')}"
            ),
            data={
                "manifest": manifest,
                "provider": result.get("provider"),
                "model": result.get("model"),
            },
        )
        return _json({
            "success": True,
            "artifact_id": artifact["artifact_id"],
            "manifest": manifest,
            "fidelity_requirements": manifest.get("fidelity_requirements") or [],
            "evidence_id": evidence.get("evidence_id"),
        })
    except (ValidationError, Exception) as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _handle_artifact_normalize(args: dict[str, Any], **_: Any) -> str:
    try:
        params = ArtifactNormalizeParams.model_validate(args)
        _reject_hermes_native_ids([params.artifact_id], "artifact_normalize")
        artifact = _load_artifact(params.artifact_id)
        normalized = _build_normalized_schema(
            artifact,
            schema_profile=params.schema_profile,
            intent=params.intent,
            semantic_schema=params.semantic_schema,
            hard_requirements=params.hard_requirements,
            advisory_preferences=params.advisory_preferences,
            evidence_ids=params.evidence_ids,
        )
        reviewed_fields = bool(
            params.semantic_schema or params.hard_requirements or params.advisory_preferences
        )
        evidence = _store_evidence(
            artifact,
            extractor="artifact_normalize",
            claim_level="reviewed" if reviewed_fields else "verified",
            summary=f"Normalized {artifact.get('adapter', 'unknown')} artifact to {ARTIFACT_SCHEMA_VERSION}.",
            data=normalized,
        )
        return _json({"success": True, "artifact_id": artifact["artifact_id"], "schema_evidence": evidence})
    except (ValidationError, Exception) as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _handle_artifact_transform(args: dict[str, Any], **_: Any) -> str:
    try:
        params = ArtifactTransformParams.model_validate(args)
        _reject_hermes_native_ids(params.source_artifact_ids, "artifact_transform")
        source_artifacts = [_load_artifact(aid) for aid in params.source_artifact_ids]

        if params.operation == "exact_copy":
            result = _exact_copy_transform(
                source_artifacts=source_artifacts,
                contract_id=params.contract_id,
                instructions=params.instructions,
            )
            return _json({"success": True, **result})

        if params.operation == "render_schema":
            result = _render_schema_transform(
                source_artifacts=source_artifacts,
                contract_id=params.contract_id,
                instructions=params.instructions,
                output_type=params.output_type,
                schema_evidence_id=params.schema_evidence_id,
                schema=params.inline_schema,
                render_title=params.render_title,
            )
            return _json({"success": True, **result})

        # edit_image: build structured brief, apply retry escalation
        prompt = _build_edit_image_prompt(params)

        if not prompt:
            raise ValueError("instructions (or structured brief fields) are required for edit_image")
        for artifact in source_artifacts:
            if artifact.get("adapter") != "image":
                raise ValueError(
                    f"edit_image requires image artifacts, got "
                    f"{artifact.get('artifact_id')} adapter={artifact.get('adapter')}"
                )
        _resolve_provider_route(params.provider_route)
        result = _openai_codex_image_generate(
            source_artifacts=source_artifacts,
            prompt=prompt,
            contract_id=params.contract_id,
            quality=params.quality,
            size=params.size,
            output_format=params.output_format,
            fidelity_mode=params.fidelity_mode,
        )
        return _json({"success": True, **result})
    except (ValidationError, Exception) as exc:
        return _json({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def _build_edit_image_prompt(params: ArtifactTransformParams) -> str:
    """Build the final provider prompt for edit_image without making provider calls."""
    retry_lines: list[str] = []
    retry_hard_preserve: list[str] = []
    retry_forbidden: list[str] = []
    if params.prior_blocker:
        retry_lines.append(f"Prior blocked review: {params.prior_blocker}")
        retry_hard_preserve.append(f"Do not repeat prior blocked review failure: {params.prior_blocker}")
        retry_forbidden.append(f"Do not repeat prior blocked review failure: {params.prior_blocker}")
    if params.retry_guidance:
        retry_lines.append(f"Required correction: {params.retry_guidance}")
        retry_hard_preserve.append(f"Required retry correction: {params.retry_guidance}")

    hard_preserve = retry_hard_preserve + list(params.hard_preserve)
    forbidden = retry_forbidden + list(params.forbidden_elements)

    parts: list[str] = []
    if retry_lines:
        parts.append("CRITICAL RETRY CORRECTION:\n" + "\n".join(f"  - {s}" for s in retry_lines))
    if params.camera_brief:
        parts.append(f"CAMERA: {params.camera_brief}")
    if params.light_brief:
        parts.append(f"LIGHT: {params.light_brief}")
    if params.mood_brief:
        parts.append(f"MOOD: {params.mood_brief}")
    if params.subject_inventory:
        parts.append("SUBJECT:\n" + "\n".join(f"  - {s}" for s in params.subject_inventory))
    if hard_preserve:
        parts.append("HARD PRESERVE:\n" + "\n".join(f"  - {s}" for s in hard_preserve))
    if forbidden:
        parts.append("FORBIDDEN:\n" + "\n".join(f"  - {s}" for s in forbidden))

    structured = "\n".join(parts)
    return (structured + "\n" + params.instructions).strip() if params.instructions else structured


def _handle_artifact_review(args: dict[str, Any], **_: Any) -> str:
    try:
        params = ArtifactReviewParams.model_validate(args)
        _reject_hermes_native_ids(params.source_artifact_ids + [params.output_artifact_id], "artifact_review")
        sources = [_load_artifact(aid) for aid in params.source_artifact_ids]
        output = _load_artifact(params.output_artifact_id)
        review = _build_review(
            contract_id=params.contract_id,
            sources=sources,
            output=output,
            provided_evidence_ids=params.evidence_ids,
            fidelity_requirements=params.fidelity_requirements,
            use_openai_vision=params.use_openai_vision,
            require_vision_api=params.require_vision_api,
            review_provider_route=params.review_provider_route,
        )
        review_path = _reviews_dir() / f"{review['review_id']}.json"
        _write_json(review_path, review)
        artifact_review_path = _artifact_root() / "reviews" / f"{review['review_id']}.json"
        _write_json(artifact_review_path, review)

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
    except (ValidationError, Exception) as exc:
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
    # An axis FAILS only when its severity is blocking/medium/high.
    # compare_status="conflict" with severity="none" means informational only —
    # it must NOT propagate as a gate failure.
    axes_map = {
        ax.get("axis"): ax.get("severity") not in ("blocking", "medium", "high")
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
