---
name: botji-2d-to-3d
description: 2D-to-3D fidelity pipeline for any source artifact (image, DXF, PDF, SVG, IFC, sketch) — schema-first extraction, zone alignment check, source-aware transform route, and mandatory 8-step review before delivery.
tags:
  - botji
  - fidelity
  - 3d
  - transform
  - artifact-fidelity
---

# Botji 2D-to-3D Fidelity Skill

Use this skill when the user provides a 2D source artifact (image, DXF, PDF, SVG, IFC, technical drawing, plan, elevation, or sketch) and asks for a 3D render, model, visualization, or perspective view where fidelity to the source geometry matters.

## Core rule

Schema first. Do not generate 3D output from a text prompt alone when a 2D source artifact with readable dimensions exists. Extract a geometry schema, confirm uncertain measurements with the user, then drive output from the schema — not from the prompt.

**User override:** If the user explicitly says "just generate something" or "don't worry about exact dimensions", set `schema_authority: user_waived`, lower `final_claim_level` to `draft`, and proceed with best-effort generation. Disclose the waiver in the review.

## Pipeline (mandatory for all 2D→3D work)

```
2D source artifact
  → 1. artifact_register          — register source file; get artifact_id and sha256
  → 2. artifact_extract           — extract geometry/schema evidence from source
  → 3. schema_validate            — validate dimension sums, positions, object counts
  → 4. user_confirm               — confirm schema when any measurement is inferred, not parsed
  → 5. artifact_normalize         — emit botji.artifact_schema.v1 as the single transform contract
  → 6. artifact_transform         — drive 3D/render output from the schema contract
  → 7. artifact_review            — compare 3D output against source schema; emit review JSON
  → 8. persist lineage            — source → schema → render → review all linked
```

Skip no step. If a step cannot be completed (tool unavailable, parse failure, missing measurement), stop and disclose before proceeding to the next step.

## Schema authority rule

The extracted and user-confirmed schema is the geometry authority. Any 3D transform must treat the schema as a hard constraint, not a suggestion. The following schema fields are always hard requirements:

- Module count and order
- Module dimensions (widths, heights, depths) in the declared unit
- Object positions (e.g. sink, cooktop, door, window) by module index
- Total run width / bounding box (must equal sum of module widths)
- Zone boundaries (e.g. upper vs lower cabinet grids must align unless explicitly flagged)

The following are advisory unless the user made them mandatory:

- Material, finish, color, texture
- Lighting, camera angle, shadow style
- Exact photorealism vs concept quality

## Zone alignment rule

When the source has multiple horizontal zones (e.g. upper wall cabinets + lower base cabinets, floor plan + elevation), the vertical module boundaries must align between zones, or the misalignment must be:

1. Present in the source drawing, AND
2. Explicitly confirmed by the user as intentional before the 3D transform is run.

If boundaries differ between zones and there is no user confirmation, block the pipeline at step 4 and ask:

> "The [upper/lower] zone boundaries don't align. Top: [dims]. Bottom: [dims]. Is this intentional in your design, or should one set be corrected before the 3D render?"

## 3D transform routes (in priority order)

1. **Schema-render** (`artifact_transform(operation: "render_schema")`) — deterministic SVG or geometry-first preview. Always run this first for any professional layout. This is the 100%-fidelity bridge between 2D schema and 3D output.
2. **Source-image edit** (`artifact_transform(operation: "edit_image")`) — pass source image as pixel reference into a provider edit API. Use only when a confirmed schema exists and the change list is explicit. This produces a reviewed (not verified) output.
3. **Schema-driven 3D engine** — Blender headless, FreeCAD, or equivalent, driven by schema dimensions. This is the gold standard for verified geometry. Use when the container has a 3D engine available.
4. **Concept generation** (`image_generate`) — allowed only when the Prompt Contract sets `visual_mode: concept_generation` and there is no source geometry to preserve. Never use this for fidelity work.

If route 1 or 2 fails, block and disclose the failure. Do not silently fall back to a lower-fidelity route.

## Fidelity scores

Persist `fidelity_scores` in every review:

```json
{
  "fidelity_scores": {
    "schema_extraction_confidence": "verified|reviewed|inferred",
    "zone_alignment_confirmed": true,
    "transform_contract_fidelity_percent": 100,
    "byte_exact_file_fidelity_percent": 0,
    "claim_type": "transform_contract_fidelity",
    "preservation_target": "source_constraint_preservation",
    "basis": ["artifact_registry", "schema_extraction", "artifact_transform_route", "artifact_review"]
  }
}
```

`transform_contract_fidelity_percent: 100` means every hard schema requirement passed. It does not mean byte-exact pixel identity.

## Prompt Contract additions for 2D→3D work

Add to the contract:

```json
{
  "transform_type": "2d_to_3d",
  "source_type": "image|dxf|pdf|svg|ifc|sketch",
  "schema_authority": "extracted|user_provided|inferred",
  "zone_alignment_confirmed": false,
  "selected_route": "render_schema|edit_image|3d_engine|concept_generation",
  "forbidden_routes": ["image_generate"],
  "required_verification_steps": ["schema_extraction", "zone_alignment_check", "artifact_review"]
}
```

## Review axes for 2D→3D

Always include the 8 base axes plus:

- `layout_fidelity` — did the 3D output preserve zone boundaries and module order?
- `geometry_fidelity` — are proportions consistent with the schema dimensions?
- `content_fidelity` — are all objects, appliances, labels, and structures present?
- `adapter_route` — was the correct source-aware route used?
- `lineage_integrity` — are source → schema → render → review all linked?
- `unknowns_handling` — are all inferred dimensions and assumptions disclosed?

## Blocking rules specific to 2D→3D

Block before final send when:

- Schema was not extracted; prompt-only generation was used instead.
- Zone boundaries differ and user has not confirmed the misalignment.
- The selected route failed and was replaced by a lower-fidelity fallback.
- `transform_contract_fidelity_percent` is not 100 and no user approval was obtained.
- The 3D output has more, fewer, or reordered modules compared to the source schema.
- Any hard schema requirement (dimension, position, count) is unmet.

## Quality gates before final 3D delivery

1. Was the source artifact registered before any generation step?
2. Was a schema extracted and validated (dimension sums check out)?
3. Were zone boundaries confirmed as aligned or intentionally misaligned?
4. Did the selected route match the contract (no silent fallback)?
5. Was the 3D output compared against the schema before delivery?
6. Are all inferred assumptions labeled as such in the review?
7. Is `transform_contract_fidelity_percent: 100` supported by evidence?
