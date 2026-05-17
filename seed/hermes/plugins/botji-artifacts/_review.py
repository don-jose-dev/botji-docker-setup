"""Fidelity review logic: comparators, axes, and review assembly."""
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
from _constants import CORE_REVIEW_AXES, ARTIFACT_SCHEMA_VERSION
from _utils import _json, _now, _new_id, _sha256, _artifact_root, _hermes_home
from _registry import _load_artifact, _store_evidence, _write_json
from _vision import (
    _vision_review_prompt, _assess_vision_payload, _coerce_review_items,
)
from _codex import (
    _codex_vision_compare, _resolve_review_provider_route,
)
from _rendering import _resolve_schema_payload
from _extraction import _deterministic_schema_for_artifact


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
        # When the artifact record itself attests the correct provider/model/quality/endpoint,
        # missing route evidence is a warning rather than a hard blocker. Full route evidence
        # is the gold standard but the artifact metadata is sufficient for a warn-level review.
        if provider == "openai-codex" and model == "gpt-image-2" and quality == "high" and endpoint:
            warnings.append("provider transform route evidence is missing; artifact record attests provider/model/quality/endpoint")
        else:
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
    # Deterministic schema renders do not produce a visual derivative of the source —
    # they produce a blockout/wireframe diagram. Visual similarity review against the
    # source image is inapplicable and must be skipped to avoid false-positive blocks.
    is_schema_render = (route == "artifact_transform.render_schema")
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
    if (
        route != "artifact_transform.exact_copy"
        and not is_schema_render
        and use_openai_vision
        and output.get("adapter") == "image"
        and all(source.get("adapter") == "image" for source in sources)
    ):
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
    elif is_schema_render:
        vision_note = (
            "Schema render output: visual similarity review against source is not applicable "
            "and has been skipped. Schema fidelity is evaluated structurally via modality comparators."
        )

    modality_result = _run_modality_comparators(
        sources=sources,
        output=output,
        provided_evidence_ids=provided_evidence_ids,
        fidelity_requirements=fidelity_requirements,
    )
    all_evidence.append(modality_result["evidence_id"])
    modality_note = modality_result["summary"]
    modality_warning = modality_result["status"] == "partial"

    # For image-to-image transforms (format, dimensions, and mode routinely differ between
    # a source photo and a rendered PNG) modality comparator differences are expected and
    # structural. They are recorded as evidence but must not cascade into a blocking verdict
    # on unrelated axes. Only dedicated vision evidence can block image content/layout axes.
    _image_to_image = output.get("adapter") == "image" and all(s.get("adapter") == "image" for s in sources)
    if modality_result["blockers"] and not _image_to_image:
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

    # ── Per-axis blocker categories ──────────────────────────────────────────
    # Each category drives only the axes it owns, preventing a single check from
    # cascading a "blocking" verdict across semantically unrelated axes.
    _lineage_blocked = bool(missing_lineage)
    _route_blocked = not route_ok
    _structural_blocked = _lineage_blocked or _route_blocked
    _vision_blocked = bool(vision_blockers)
    # Modality blockers are meaningful only for non-image transforms (see above).
    _modality_blocked = bool(modality_result["blockers"]) and not _image_to_image
    _provider_blocked = bool(high_fidelity_result and high_fidelity_result["blockers"])
    # ────────────────────────────────────────────────────────────────────────

    grounding_note = (
        "Output claims are grounded in registry lineage and persisted evidence."
        if not _structural_blocked
        else "Grounding failed because lineage or route is invalid."
    )
    review_warning = vision_failed or vision_warning or modality_warning
    preserve_note = exact_note or (vision_note if vision_evidence_id or vision_failed or vision_warning else modality_note)

    # preserve_change: driven by the best available content comparison evidence
    preserve_status = "conflict" if _vision_blocked else ("partial" if review_warning else "match")
    preserve_severity = "blocking" if _vision_blocked else ("medium" if vision_warning else ("low" if review_warning else "none"))

    # uncertainty: reflects completeness and consistency of review evidence, not content verdicts
    uncertainty_status = "partial" if (vision_failed or vision_warning) else ("conflict" if _structural_blocked else "match")
    uncertainty_severity = "blocking" if _structural_blocked else ("low" if (vision_failed or vision_warning) else "none")
    uncertainty_note = (
        vision_note if (vision_failed or vision_warning)
        else "Review evidence is complete and internally consistent."
        if not _structural_blocked
        else "Review cannot be completed due to lineage or route errors."
    )

    # transform_contract: blocked by structural issues OR hard vision conflicts
    _transform_blocked = _structural_blocked or _vision_blocked
    transform_contract_status = "conflict" if _transform_blocked else ("partial" if review_warning else "match")
    transform_contract_percent = 0 if _transform_blocked else (95 if review_warning else 100)

    exact_match = bool(exact_result and exact_result["status"] == "match")
    byte_exact_percent = 100 if exact_match else 0

    if exact_match:
        claim_type = "byte_exact_file_fidelity"
        preservation_target = "byte_exact_file_identity"
    elif high_fidelity_result:
        claim_type = "transform_contract_fidelity"
        preservation_target = "source_constraint_preservation"
    elif is_schema_render:
        claim_type = "transform_contract_fidelity"
        preservation_target = "deterministic_schema_equivalence"
    else:
        claim_type = "reviewed_visual_similarity"
        preservation_target = "visual_reference_similarity"

    transform_contract_note = (
        "Output is byte-identical to the source artifact; both byte-exact and transform-contract fidelity are 100%."
        if exact_match
        else "All hard source requirements passed under the allowed transform contract; this is 100% transform-contract fidelity, not byte-exact file identity."
        if transform_contract_status == "match" and not exact_match
        else "Transform contract has non-blocking review warnings; it is not a 100% transform-fidelity pass."
        if transform_contract_status == "partial"
        else "Transform contract has blocking source-fidelity conflicts."
    )

    # geometry_fidelity: for raster image outputs only vision evidence constitutes a hard
    # geometry block; modality metadata differences are structural and non-blocking.
    if _image_to_image:
        geometry_status = "conflict" if _vision_blocked else ("match" if exact_match else "partial")
        geometry_severity = "blocking" if _vision_blocked else ("none" if exact_match else "low")
    else:
        geometry_status = (
            "conflict" if (_vision_blocked or _modality_blocked or (exact_result and exact_result["status"] == "conflict"))
            else "match" if (exact_match or (is_schema_render and modality_result["status"] == "match"))
            else "partial"
        )
        geometry_severity = "blocking" if geometry_status == "conflict" else ("none" if geometry_status == "match" else "low")

    # content_fidelity: owned by vision evidence alone; modality metadata ≠ visual content
    content_status = "conflict" if _vision_blocked else ("partial" if review_warning and not _vision_blocked else "match")
    content_severity = "blocking" if _vision_blocked else ("low" if review_warning else "none")

    # layout_fidelity: vision + geometry (for non-image: also structural modality drift)
    layout_blocked = _vision_blocked or (geometry_status == "conflict" and not _image_to_image)
    layout_status = "conflict" if layout_blocked else ("partial" if review_warning else "match")
    layout_severity = "blocking" if layout_blocked else ("low" if review_warning else "none")

    # modality_comparator axis severity: for image-to-image treat as informational, not blocking
    modality_axis_severity = (
        "none" if _image_to_image and modality_result["status"] == "conflict"
        else "blocking" if modality_result["status"] == "conflict"
        else "low" if modality_warning
        else "none"
    )

    deterministic_claim = "verified" if all_evidence else "reviewed"
    axes.extend([
        _axis("source_coverage", "match" if sources else "missing", "none" if sources else "blocking", "Source artifact IDs are present." if sources else "No source artifacts were supplied.", evidence_ids=all_evidence),
        _axis("authority_alignment", "match", "none", "User-supplied source artifacts outrank generated output assumptions.", evidence_ids=all_evidence),
        _axis("preserve_change", preserve_status, preserve_severity, preserve_note, evidence_ids=all_evidence),
        _axis("groundedness", "conflict" if _structural_blocked else "match", "blocking" if _structural_blocked else "none", grounding_note, evidence_ids=all_evidence),
        _axis("uncertainty", uncertainty_status, uncertainty_severity, uncertainty_note, evidence_ids=all_evidence),
        _axis("safety", "match", "none", "Artifact paths were constrained to Botji safe roots; no credential paths were used.", claim_level=deterministic_claim, evidence_ids=all_evidence),
        _axis("artifact_lineage", "conflict" if _lineage_blocked else "match", "blocking" if _lineage_blocked else "none", "Output parents include all source artifacts." if not _lineage_blocked else "Output lineage is incomplete.", claim_level=deterministic_claim, evidence_ids=all_evidence),
        _axis("actionability", "missing" if _structural_blocked else "match", "blocking" if _structural_blocked else "none", "Correction required: lineage or route error must be resolved before this artifact is usable." if _structural_blocked else "Review receipt and output artifact path are persisted.", evidence_ids=all_evidence),
        _axis("adapter_route", "match" if route_ok else "conflict", "none" if route_ok else "blocking", "Source-aware artifact route was used." if route_ok else "Prompt-only, manually registered, or unknown transform route was used.", claim_level=deterministic_claim, evidence_ids=all_evidence),
        _axis("lineage_integrity", "match" if not _lineage_blocked else "conflict", "none" if not _lineage_blocked else "blocking", "Parent artifact IDs match the source list." if not _lineage_blocked else "Parent artifact IDs are missing.", claim_level=deterministic_claim, evidence_ids=all_evidence),
        _axis("modality_comparator", modality_result["status"], modality_axis_severity, modality_note, claim_level="verified" if modality_result["status"] == "match" else "reviewed", evidence_ids=[modality_result["evidence_id"]]),
        *([_axis("high_fidelity_provider_transform", high_fidelity_result["status"], "blocking" if high_fidelity_result["status"] == "conflict" else ("low" if high_fidelity_result["status"] == "partial" else "none"), high_fidelity_result["summary"], claim_level="reviewed", evidence_ids=[high_fidelity_result["evidence_id"]])] if high_fidelity_result else []),
        *([_axis("metadata_fidelity", exact_result["status"], "blocking" if exact_result["status"] == "conflict" else "none", exact_note, claim_level="verified" if exact_result["status"] == "match" else "reviewed", evidence_ids=[exact_result["evidence_id"]])] if exact_result else []),
        _axis("transform_contract_fidelity", transform_contract_status, "blocking" if _transform_blocked else ("low" if review_warning else "none"), transform_contract_note, claim_level="verified" if exact_match else "reviewed", evidence_ids=all_evidence),
        _axis("content_fidelity", content_status, content_severity, preserve_note, evidence_ids=[exact_result["evidence_id"]] if exact_result else ([vision_evidence_id] if vision_evidence_id else [modality_result["evidence_id"]])),
        _axis("layout_fidelity", layout_status, layout_severity, preserve_note, evidence_ids=[exact_result["evidence_id"]] if exact_result else ([vision_evidence_id] if vision_evidence_id else [modality_result["evidence_id"]])),
        _axis("geometry_fidelity", geometry_status, geometry_severity, _geometry_fidelity_note(output), evidence_ids=all_evidence),
        _axis("unknowns_handling", "match", "none", "Exact physical dimensions are not claimed unless supplied by deterministic source evidence.", evidence_ids=all_evidence),
    ])
    verdict = "block" if blockers else ("warn" if review_warning else "pass")
    final_claim_level = "verified" if exact_match and not blockers else "reviewed"

    # Delivery gate fields: agent-facing signal to prevent shipping blocked outputs.
    delivery_gate = "blocked" if verdict == "block" else ("warned" if verdict == "warn" else "clear")
    recommended_action = (
        "do_not_deliver__retry_with_corrections"
        if verdict == "block"
        else "deliver_with_warning"
        if verdict == "warn"
        else "deliver"
    )
    primary_blocker = blockers[0] if blockers else None
    retry_guidance = corrections[0] if blockers and corrections else None

    return {
        "review_id": review_id,
        "contract_id": contract_id,
        "reviewed_output_ref": output["artifact_id"],
        "verdict": verdict,
        "delivery_gate": delivery_gate,
        "recommended_action": recommended_action,
        "primary_blocker": primary_blocker,
        "retry_guidance": retry_guidance,
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


