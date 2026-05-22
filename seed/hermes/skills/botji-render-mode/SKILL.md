---
name: botji-render-mode
description: Fidelity-transform mode selection (photo / technical / spec / concept) and the per-mode pipelines, review thresholds, and delivery format for source-bound render work. Use whenever the user has provided a source image, drawing, or dimensions and wants a render, edit, or 3D output.
tags:
  - botji
  - render
  - fidelity
  - photo-mode
  - artifact-fidelity
---

# Botji Render-Mode Skill

Use this skill whenever the user has supplied a source artifact (photo, sketch, CAD file, dimensions) and wants a render, edit, or 3D output. This is the **mode-selection layer** for source-bound render work — it decides between photo / technical / spec / concept pipelines and owns the per-mode rules and review thresholds.

Prerequisites: `artifact_register` for the source must already have been called (see `botji-artifact-fidelity` for the generic loop).

## Retry budget — canonical

**Maximum 2 `artifact_transform(operation="edit_image")` calls per user turn.** This is the canonical cap. It applies regardless of which skill the agent is reading (`botji-render-router`, `botji-2d-to-3d`, `botji-codex-engineering`) — when they reference a retry, they reference *this* number.

The shape is always *1 attempt + at most 1 retry*. If the retry also blocks, stop and ask the user (accept, adjust source, or try a different camera angle). Never silently run a third transform.

Exceptions, both explicit:

- The user asked for N variants in this turn — then N transforms are allowed (one per variant; no retries inside a variant slot).
- The user explicitly asked to retry past the cap. Disclose the budget burn.

Why 2 and not 3: a single `edit_image` is ~60–90 s. Three burns the Telegram-UX speed budget and rarely converges on the same block twice.

## Choose the right mode

| Source type | Mode | Steps | Where the pipeline lives |
|---|---|---|---|
| Phone photo, render, screenshot, reference image | **Photo mode** | 4 | this skill |
| Sketch / floor plan / 2D schematic | **Sketch mode** | spatial-manifest pipeline | `botji-2d-to-3d` |
| DXF / PDF / IFC / SVG with parseable geometry | **Technical mode** | 8 | this skill (summary) + `botji-2d-to-3d` for the full sketch path |
| User provides only dimensions, no drawing | **Spec mode** | 4 | this skill |
| User says "just generate" or waives fidelity | **Concept mode** | 1 | this skill |

If the user asks for a CAD / DXF / DWG deliverable rather than a render, switch to `botji-cad-elevation` instead — `edit_image` is forbidden for CAD output.

## Photo mode pipeline (~60–90s)

Use when source is a raster image. Do NOT run `artifact_extract`, `artifact_normalize`, `schema_validate`, or `user_confirm` on a photo — those steps are for technical drawings only.

```
1. artifact_register(path, role="source", declared_type="image", current_turn_id=...)

2. artifact_transform(
     operation="edit_image",
     source_artifact_ids=[source_id],
     contract_id=contract_id,
     camera_brief="[body · lens · view angle]",
     light_brief="[quality · direction · color temp]",
     mood_brief="[photography/rendering genre]",
     subject_inventory=["[element 1 with count and position]", "[element 2]"],
     hard_preserve=[
       "Overall spatial layout and composition",
       "Object count: exactly [N] [primary elements]",
       "[named structural constraints from source]"
     ],
     forbidden_elements=[
       "Do not add any objects not visible in the source image",
       "Do not add plants, furniture, decor, people, or clutter not in source",
       "Do not remove or reorder structural elements",
       "Do not add extra [panels/shelves/elements] beyond source",
       "Do not extend any element beyond its source boundary"
     ]
   )

3. artifact_review(
     source_artifact_ids=[source_id],
     output_artifact_id=output_id,
     fidelity_requirements=hard_preserve,
     use_openai_vision=True
   )

4. Deliver: send image first, then review badge.
```

Use structured brief fields (camera_brief / light_brief / mood_brief / subject_inventory / hard_preserve / forbidden_elements) — NOT a freeform `instructions` string. The plugin assembles the final prompt. See `botji-premium-brief` for the brief vocabulary.

**If the user provides dimensions** ("Size: 2400mm × 1500mm"), add them to `hard_preserve` — do NOT run schema_validate:

```
hard_preserve=["Overall dimensions: 2400mm wide × 1500mm tall — set scale and proportions from this", ...]
```

## Technical mode pipeline

Use when source has machine-readable geometry (DXF, PDF, IFC, SVG). For sketches and floor plans, use `botji-2d-to-3d` directly — it is the full pipeline owner.

```
1. artifact_register          — register source; get artifact_id and sha256
2. artifact_extract           — extract geometry, layers, entities, dimensions
3. schema_validate            — validate dimension sums, counts, positions
4. user_confirm               — confirm inferred measurements (only if ambiguous)
5. artifact_normalize         — emit botji.artifact_schema.v1 as transform contract
6. artifact_transform         — drive output from schema contract
7. artifact_review            — compare output against source schema
8. persist lineage            — source → schema → render → review all linked
```

Schema is the geometry authority. The render must match the schema, not just look similar.

**Zone alignment rule:** for multi-zone layouts (upper/lower zones, floor plan + elevation), vertical element boundaries must align or the user must confirm: *"The zone boundaries don't align. Top: [dims]. Bottom: [dims]. Intentional?"*

## Spec mode (dimensions only, no drawing)

```
1. Build dimensional spec from user text (no artifact_register needed).
2. image_generate with visual brief including spec dimensions.
3. Brief visual comparison against spec.
4. Deliver with claim level "reviewed".
```

Set `schema_authority: user_provided`. Disclose dimensions are interpreted, not extracted.

## Concept mode (user waives fidelity)

Set `schema_authority: user_waived`, `final_claim_level: draft`. One step: `image_generate` with style brief.

## Photo-mode fidelity review thresholds

| Review result | Action |
|---|---|
| `preserve_change: conflict` + objects added not in source | Block — regenerate with stricter FORBIDDEN list |
| `preserve_change: conflict` + layout/count wrong | Block — regenerate |
| `preserve_change: conflict` + minor proportion/style drift | **Warn only** — deliver with caveat |
| `preserve_change: partial` + style differences only | Pass — deliver with honest caption |
| `groundedness: conflict` due to invalid route | Fix route evidence, re-review |

Do not auto-block for minor style drift. Block when object count changes or source elements are invented or removed.

Photo-mode claim level is always `reviewed`. Never `verified` for an image edit.

## Sketch-to-render review thresholds

For sketch sources, the full pipeline lives in `botji-2d-to-3d`. The thresholds below are the *review-side* contract — the review authority is the **spatial manifest** (element count, order, adjacency), NOT pixel geometry. A hand-drawn sketch will never geometrically match a 3D photorealistic render — dimensional and proportion differences are expected.

| What changed | Action |
|---|---|
| Element count changed | Block |
| Element left-to-right order changed | Block |
| Adjacency violated (gap or filler inserted between adjacent elements) | Block |
| Object added or removed | Block |
| Proportions / depth / perspective differ from sketch | **Warn only — do not block** |
| Lighting, material, or finish drift | Pass |
| Sketch line details not replicated | Pass |

If `delivery_gate == "blocked"` and ALL conflicts are proportion/geometry drift (no count/order/adjacency violations), override the gate and deliver with a warn caveat. The gate is for spatial violations, not artistic interpretation differences.

## Delivery

Send the image first, then the badge:

```
✅ 3D render · route: edit_image · claim: reviewed · fidelity: [N]%
[1-line match/conflict summary]
```

## Companion skills

- `botji-artifact-fidelity` — the generic register/extract/normalize/transform/review loop. This skill adds the mode-specific shape on top.
- `botji-premium-brief` — the camera / light / materials / mood / reference / signature vocabulary used in the brief fields.
- `botji-2d-to-3d` — the full spatial-manifest pipeline for sketch sources.
- `botji-source-fidelity` — the review contract used in step 3 / step 7.
- `botji-cad-elevation` — when the deliverable is a real CAD file rather than a render.
