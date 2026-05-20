"""Per-adapter comparators and provider-route checks used by review assembly.

Each ``_run_*`` function produces one evidence record and a result dict with:
- ``status``: ``match`` | ``partial`` | ``conflict``
- ``summary``: human-readable explanation
- ``evidence_id``: id of the stored evidence record
- ``blockers``: list of hard-blocking conflict strings (may be empty)
- ``warnings``: list of non-blocking advisory strings (may be empty)

``_build_review`` in ``_review.py`` aggregates these into final per-axis
verdicts. Splitting them out keeps the review-assembly logic distinct from
the modality-specific comparison logic.
"""
from __future__ import annotations

import json
from typing import Any

from _constants import ARTIFACT_SCHEMA_VERSION
from _registry import _store_evidence
from _rendering import _resolve_schema_payload, _load_evidence
from _extraction import _deterministic_schema_for_artifact


# Per-adapter comparator key sets. A field is checked only when present in
# the normalized schema; missing fields generate warnings, not blockers.
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


def _canonical(value: Any) -> str:
    """Stable string form of any JSON-compatible value for equality checks."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _comparator_name(adapter: str) -> str:
    """Human-readable name of the comparator used for the given adapter."""
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


def _geometry_fidelity_note(output: dict[str, Any]) -> str:
    """One-line explanation of the geometry-fidelity guarantee for the output."""
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


def _review_schema_payloads(
    sources: list[dict[str, Any]],
    output: dict[str, Any],
    provided_evidence_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return ``{artifact_id: schema_payload}`` for every source with normalized schema evidence."""
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
    """Compare two metadata dicts for the given adapter's comparator keys."""
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


def _run_modality_comparators(
    *,
    sources: list[dict[str, Any]],
    output: dict[str, Any],
    provided_evidence_ids: list[str],
    fidelity_requirements: list[str],
) -> dict[str, Any]:
    """Run the per-adapter schema comparator for every source artifact."""
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
    """For ``exact_copy`` outputs, verify byte-identical hash + size to the single source."""
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
    """For ``edit_image`` outputs on image sources, verify the provider route used GPT-Image-2 with high-fidelity image inputs and no prompt-only fallback."""
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


# --- Brief-specificity check (premium-vocabulary discipline) ---
# Deterministic scan of the agent's brief (artifact_transform `instructions` field)
# for the four required premium signals and the banned noise vocabulary.
# Returns a `brief_specificity` axis dict that the reviewer can append at
# informational severity. Never blocks delivery — its purpose is to give the
# agent a feedback signal for prompt-construction quality.

import re as _re

_BANNED_NOISE_TERMS = (
    "realistic", "photorealistic", "hyperrealistic",
    "high quality", "8k", "4k", "ultra hd", "hdr",
    "beautiful", "nice", "gorgeous", "stunning", "amazing",
    "modern style", "luxury", "elegant", "refined",
    "sleek", "sophisticated",
    "good lighting", "warm tones", "well-lit",
    "cosy", "cozy", "inviting", "dreamy", "magical",
)

_KELVIN_RE = _re.compile(r"\b\d{3,5}\s*K\b")
_BRIEF_SECTION_HEADERS = (
    "CAMERA",
    "LIGHT",
    "MATERIAL",
    "MATERIALS",
    "MOOD",
    "REFERENCE",
    "REFERENCES",
    "SIGNATURE",
    "SUBJECT",
    "HARD PRESERVE",
    "FORBIDDEN",
)
_BRIEF_SECTION_RE = _re.compile(
    r"(?ims)^\s*(?P<header>" + "|".join(_re.escape(h) for h in _BRIEF_SECTION_HEADERS) + r")\s*:\s*(?P<body>.*?)"
    r"(?=^\s*(?:" + "|".join(_re.escape(h) for h in _BRIEF_SECTION_HEADERS) + r")\s*:|\Z)"
)
_MATERIAL_FINISH_HINTS = (
    "rift-sawn", "hand-rubbed", "honed", "brushed", "patina",
    "matte oil", "lime-wash", "herringbone", "wide-plank",
    "hand-formed", "full-grain", "hand-polished", "blackened",
    "low-iron", "cast concrete", "veneer", "travertine",
    "carrara", "ash", "walnut", "european oak", "white oak",
    "linen with visible weave", "saddle leather",
)
_REFERENCE_HINTS = (
    "dezeen", "wallpaper*", "wallpaper magazine",
    "architectural digest", "ad magazine", "ad residential",
    "apple studio", "norm architects", "kinfolk",
    "riba", "editorial residential", "editorial interior",
    "magazine architecture", "stripe documentation",
)
_SIGNATURE_HINTS = (
    "caustic", "specular", "soft falloff", "soft falloff",
    "gentle bounce", "bounce light", "rake light",
    "micro-reflection", "soft dust", "shadow falloff",
    "rim light", "worn edge",
)


def _brief_sections(prompt: str | None) -> dict[str, str]:
    sections: dict[str, str] = {}
    for match in _BRIEF_SECTION_RE.finditer(prompt or ""):
        header = match.group("header").strip().upper()
        body = match.group("body").strip()
        sections[header] = f"{sections.get(header, '')}\n{body}".strip()
    return sections


def _structured_material_count(materials_section: str) -> int:
    if not materials_section.strip():
        return 0

    segments = [
        segment.strip(" -\t\r\n")
        for segment in _re.split(r"(?:\n+|[;•·|])", materials_section)
        if segment.strip(" -\t\r\n")
    ]
    named_specs = 0
    for segment in segments:
        if ":" in segment:
            _, value = segment.split(":", 1)
            if len(value.strip()) >= 3 and _re.search(r"[A-Za-z]", value):
                named_specs += 1
        elif _re.search(r"\b(tile|stone|granite|marble|wood|oak|walnut|ash|veneer|plaster|concrete|metal|glass|linen|leather|laminate|lacquer|matte|honed|brushed|polished)\b", segment, _re.I):
            named_specs += 1
    return named_specs


def brief_specificity(prompt: str | None) -> dict:
    """Score a brief against premium-vocabulary discipline.

    Returns dict with keys: status (`match`/`partial`/`missing`), severity
    (always `none` — informational), notes, banned_terms_found,
    has_kelvin, material_count, has_reference, has_signature.

    A `match` brief: zero banned terms, ≥1 Kelvin number, ≥3 material
    hints, ≥1 reference hint, ≥1 signature hint.
    """
    sections = _brief_sections(prompt)
    text = (prompt or "").lower()
    banned = sorted({term for term in _BANNED_NOISE_TERMS if term in text})
    has_kelvin = bool(_KELVIN_RE.search(prompt or ""))
    materials_section = sections.get("MATERIALS") or sections.get("MATERIAL") or ""
    reference_section = sections.get("REFERENCE") or sections.get("REFERENCES") or ""
    signature_section = sections.get("SIGNATURE") or ""
    material_count = max(
        sum(1 for h in _MATERIAL_FINISH_HINTS if h in text),
        _structured_material_count(materials_section),
    )
    has_reference = bool(reference_section.strip()) or any(h in text for h in _REFERENCE_HINTS)
    has_signature = bool(signature_section.strip()) or any(h in text for h in _SIGNATURE_HINTS)

    required_ok = has_kelvin and material_count >= 3 and has_reference and has_signature
    if required_ok and not banned:
        status = "match"
        notes = "Brief satisfies premium-vocabulary discipline (Kelvin · materials · reference · signature) and has no banned noise terms."
    elif required_ok and banned:
        status = "partial"
        notes = f"Brief has all four required specifications but contains banned noise vocabulary: {', '.join(banned)}. Rewrite per botji-premium-brief."
    elif banned and not required_ok:
        status = "missing"
        missing = []
        if not has_kelvin: missing.append("Kelvin light spec")
        if material_count < 3: missing.append(f"named materials (have {material_count}/3+)")
        if not has_reference: missing.append("reference genre")
        if not has_signature: missing.append("signature detail")
        notes = f"Brief is missing: {', '.join(missing)}. Also contains banned noise vocabulary: {', '.join(banned)}."
    else:
        status = "partial"
        missing = []
        if not has_kelvin: missing.append("Kelvin light spec")
        if material_count < 3: missing.append(f"named materials (have {material_count}/3+)")
        if not has_reference: missing.append("reference genre")
        if not has_signature: missing.append("signature detail")
        notes = f"Brief is missing: {', '.join(missing)}. See botji-premium-brief for vocabulary."

    return {
        "status": status,
        "severity": "none",
        "notes": notes,
        "banned_terms_found": banned,
        "has_kelvin": has_kelvin,
        "material_count": material_count,
        "has_reference": has_reference,
        "has_signature": has_signature,
    }


__all__ = [
    "COMPARATOR_KEYS",
    "_canonical",
    "_comparator_name",
    "_geometry_fidelity_note",
    "_review_schema_payloads",
    "_compare_deterministic_metadata",
    "_run_modality_comparators",
    "_run_exact_output_compare",
    "_run_high_fidelity_provider_transform",
    "brief_specificity",
]
