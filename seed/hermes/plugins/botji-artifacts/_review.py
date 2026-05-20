"""Fidelity review assembly — composes evidence from ``_comparators`` into axes + verdict.

The comparator functions live in ``_comparators.py``. This module's job is:
1. Run them in the right order, conditional on output route + adapter
2. Run the codex vision compare when sources and output are both images
3. Derive each axis verdict from the collected evidence
4. Assemble the final review object

Splitting comparator implementations into their own module keeps the review-assembly
logic readable. This file used to be 683 lines (a god module); now it's focused
on orchestration only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from _utils import _new_id, _hermes_home
from _registry import _store_evidence
from _vision import _assess_vision_payload
from _codex import _codex_vision_compare, _resolve_review_provider_route
from _comparators import (
    _geometry_fidelity_note,
    _run_modality_comparators,
    _run_exact_output_compare,
    _run_high_fidelity_provider_transform,
    brief_specificity,
)


def _reviews_dir() -> Path:
    return _hermes_home() / "reviews"


def _axis(
    axis: str,
    status: str,
    severity: str,
    notes: str,
    *,
    claim_level: str = "reviewed",
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build one axis record for the review."""
    return {
        "axis": axis,
        "compare_status": status,
        "severity": severity,
        "notes": notes,
        "claim_level": claim_level,
        "evidence_ids": evidence_ids or [],
    }


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
            assessment = _assess_vision_payload(vision_payload, fidelity_requirements)
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
    # modality_comparator: conflict must hard-block regardless of route (VPS audit 2026-05:
    # 9/329 reviews were rubber-stamped to delivery_gate=clear despite comparator conflict).
    # The image-to-image suppression above prevents cascade into unrelated axes, but the
    # comparator's own conflict verdict — schema/content drift between source and output —
    # must still gate delivery. Without this, a broken transform that mangles the source
    # ships silently.
    elif modality_result["status"] == "conflict":
        blockers.append("modality comparator detected schema/content drift between source and output")
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

    # modality_comparator axis severity: a comparator conflict is always blocking (VPS audit
    # 2026-05 found this axis was silently downgraded to severity=none for image-to-image,
    # producing delivery_gate=clear despite the comparator reporting drift).
    modality_axis_severity = (
        "blocking" if modality_result["status"] == "conflict"
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

    # Informational axis: scan the agent's brief (output.user_intent) for premium-
    # vocabulary discipline. Never blocks delivery — severity is always `none` —
    # but surfaces missing Kelvin / materials / reference / signature so the
    # agent gets a feedback signal on prompt-construction quality.
    if _image_to_image:
        _brief_score = brief_specificity(output.get("user_intent") or "")
        axes.append(_axis(
            "brief_specificity",
            _brief_score["status"],
            "none",
            _brief_score["notes"],
            claim_level="reviewed",
            evidence_ids=all_evidence,
        ))
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


__all__ = ["_reviews_dir", "_axis", "_build_review"]
