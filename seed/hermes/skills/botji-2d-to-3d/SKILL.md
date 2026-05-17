---
name: botji-2d-to-3d
description: 2D-to-3D fidelity pipeline for photos, reference images, technical drawings, DXF, PDF, and sketches. Two modes — fast photo/reference path (4 steps, ~60-90s) or full technical path (8 steps) — with mandatory artifact review before delivery.
tags:
  - botji
  - fidelity
  - 3d
  - transform
  - artifact-fidelity
---

# Botji 2D-to-3D Fidelity Skill

Use this skill when the user uploads a photo, reference image, technical drawing, DXF, PDF, SVG, IFC, or sketch and asks for a 3D render, perspective view, visualization, or derivative image.

---

## Choose the right mode first — do not run the 8-step pipeline on a photo

| Source type | Mode | Steps |
|---|---|---|
| Phone photo, render, screenshot, reference image | **Photo mode** | 4 steps |
| DXF, PDF, IFC, SVG with parseable geometry | **Technical mode** | 8 steps |
| User provides only dimensions/size spec, no drawing | **Spec mode** | 4 steps |
| User says "just generate" or "don't worry about exact dims" | **Concept mode** | 2 steps |

---

## PHOTO MODE — for phone photos, reference renders, screenshots (4 steps, ~60–90s)

Use when the source is a raster image without parseable CAD geometry.

```
1. artifact_register(path, role="source", declared_type="image")
2. artifact_transform(
     operation="edit_image",
     source_artifact_ids=[source_id],
     contract_id=contract_id,
     camera_brief="[body · lens · view angle]",
     light_brief="[quality · direction · color temp]",
     mood_brief="[photography/rendering genre]",
     subject_inventory=[
       "[primary element 1 with count and position from source]",
       "[primary element 2]"
     ],
     hard_preserve=[
       "Overall spatial layout and composition",
       "Object count: exactly [N] [primary elements]",
       "[named structural constraints from source]"
     ],
     forbidden_elements=[
       "Do not add any objects not visible in the source image",
       "Do not add plants, furniture, decor, people, or clutter not in source",
       "Do not remove or reorder structural elements",
       "Do not add extra [panels/shelves/modules] beyond source",
       "Do not extend any element beyond its source boundary"
     ]
   )
3. artifact_review(source_artifact_ids=[source_id], output_artifact_id=output_id,
     fidelity_requirements=hard_preserve, use_openai_vision=True)
4. Deliver: send image first, then review badge
```

Do NOT run artifact_extract, schema_validate, artifact_normalize, or user_confirm on a photo. These steps are for technical drawings with parseable geometry only.

Use structured brief fields (`camera_brief`, `subject_inventory`, `hard_preserve`, `forbidden_elements`) — NOT a freeform `instructions` string. The plugin assembles the final prompt from these fields, ensuring quality regardless of prompt verbosity.

### Size spec with photo

If the user provides dimensions ("Size: 2400mm × 1500mm"), add to `hard_preserve` — do NOT run schema_validate:

```
hard_preserve=["Overall dimensions: 2400mm wide × 1500mm tall — set scale and proportions from this", ...]
```

### Photo mode fidelity claim

Always `final_claim_level: reviewed`. Never `verified` for an image edit.

```json
"fidelity_scores": {
  "transform_contract_fidelity_percent": 100,
  "byte_exact_file_fidelity_percent": 0,
  "claim_type": "transform_contract_fidelity",
  "schema_extraction_confidence": "inferred"
}
```

---

## TECHNICAL MODE — for DXF, PDF, IFC, SVG with parseable geometry (8 steps)

Use when the source has machine-readable geometry.

```
1. artifact_register          — register source; get artifact_id and sha256
2. artifact_extract           — extract geometry, layers, entities, dimensions
3. schema_validate            — validate dimension sums, counts, positions
4. user_confirm               — confirm inferred measurements (ask only if ambiguous)
5. artifact_normalize         — emit botji.artifact_schema.v1 as transform contract
6. artifact_transform         — drive output from schema contract
7. artifact_review            — compare output against source schema
8. persist lineage            — source → schema → render → review all linked
```

Schema is the geometry authority. The render must match the schema, not just look similar.

### Zone alignment rule

For multi-zone layouts (upper/lower cabinets, floor plan + elevation), vertical module boundaries must align or the user must confirm the mismatch:

> "The zone boundaries don't align. Top: [dims]. Bottom: [dims]. Intentional?"

---

## SPEC MODE — dimensions only, no drawing (4 steps)

When user provides size spec without a source image:

```
1. Build dimensional spec from user text (no artifact_register needed)
2. image_generate with visual brief including spec dimensions
3. Brief visual comparison against spec
4. Deliver with claim level "reviewed"
```

Set `schema_authority: user_provided`. Disclose dimensions are interpreted, not extracted.

---

## CONCEPT MODE — user waives fidelity

Set `schema_authority: user_waived`, `final_claim_level: draft`. One step: image_generate with style brief.

---

## Transform route rules

| Route | When |
|---|---|
| `artifact_transform(operation="edit_image")` | Photo source, preserve layout. PRIMARY for photo mode. |
| `artifact_transform(operation="render_schema")` | Technical drawing, schema-first preview. |
| `image_generate` | Concept mode only — no source to preserve. |

**No fallback rule:** If `edit_image` fails, stop and report. Do not switch to `image_generate` for source-bound work.

**Route evidence:** If `image_generate` was used as a fallback, call `artifact_register(route="artifact_transform.edit_image.openai_codex", parents=[source_id])` before `artifact_review` or groundedness will block.

---

## Fidelity review interpretation for photo mode

| Review result | Action |
|---|---|
| `preserve_change: conflict` + objects added that are not in source | Block — regenerate with stricter FORBIDDEN list |
| `preserve_change: conflict` + layout/count wrong | Block — regenerate |
| `preserve_change: conflict` + minor proportion/style drift | Warn user, deliver with reduced fidelity score |
| `preserve_change: partial` + style differences only | Pass — deliver with honest caption |
| `groundedness: conflict` due to invalid route | Fix route evidence, re-review |

Do not auto-block for minor style drift. Block when object count changes or source elements are invented or removed.

---

## Delivery (Telegram)

Send image first, then:

```
✅ 3D render · route: edit_image · claim: reviewed · fidelity: [N]%
[1-line match/conflict summary]
```

Show progress during generation:
```
🔄 1/3 — registering source…
🔄 2/3 — generating 3D render (~60s)…
✅ 3/3 — fidelity review complete
```

---

## Common use cases

**"Give a 3D image of [thing] Size: WxH" + photo**
→ Photo mode. Parse dimensions as HARD PRESERVE scale. 4 steps.

**"Use full wall" (follow-up)**
→ Photo mode. Update HARD PRESERVE: fill wall edge-to-edge. 1-2 tool calls.

**"Give a similar model of this 3D image"**
→ Photo mode. Source = the sent 3D image. Preserve layout, update style.

**"Create a 2D production drawing of this"**
→ 3D→2D. artifact_register + artifact_normalize(schema_profile="cad") + render_schema. Output: dimension-labeled schematic.

**"Mark measurements properly"**
→ artifact_transform(operation="render_schema") on existing schema. Add measurement labels to SVG output.
