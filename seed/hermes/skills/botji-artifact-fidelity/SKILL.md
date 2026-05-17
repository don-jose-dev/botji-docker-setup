---
name: botji-artifact-fidelity
description: Generic artifact fidelity for any file type — register, extract, normalize, transform, review. Enforces exact_copy default, schema-first rendering, and no-silent-fallback rule.
tags:
  - botji
  - artifact-fidelity
  - fidelity
  - image-fidelity
---

# Botji Artifact Fidelity Skill

Use this skill whenever the user provides or references a file, image, PDF, text document, DXF/CAD drawing, generated output, or any derivative that must stay faithful to a source.

## Core rule

Files are evidence, not decoration. Register the file as an artifact before analysis, extraction, transformation, or final claims.
The default transformation policy is exact preservation: if the user provided a source file, preserve it byte-for-byte unless the user explicitly authorizes a transform route and change list.

## Request type — decide before calling any tool

**Fidelity transform** ("make this 3D", "render this sketch", "convert to photo"): use the full pipeline. Gate fires.

**Design proposal** ("add a wardrobe here", "show what a kitchen looks like in this space", "give me a 3D image of [element] in this area"): the user explicitly wants to ADD or PLACE something. This is NOT a fidelity violation. Pipeline:
1. `artifact_register(path, role="source")`
2. `artifact_transform(operation="edit_image", ...)` — include the requested element in `subject_inventory`
3. `artifact_review(fidelity_requirements=["Allowed transform: [element] added/placed as user requested"])`
   The `"Allowed transform:"` prefix tells the review that this specific addition was user-authorised — the gate will not block it.
4. Deliver with: `✅ Design proposal · route: edit_image · claim: reviewed · [element] placed as requested`

**Concept generation** (no source image, or user says "design me X from scratch"): skip artifact_review. Claim level `draft`. Gate does not fire.

**Never use fidelity mode for a design proposal** — the review will correctly flag "element added not in source" as a hard conflict and block delivery. Use `"Allowed transform:"` in fidelity_requirements instead.

## Required loop

1. Call `artifact_register` for every source file path.
2. Call `artifact_extract` before making claims about file contents, dimensions, text, pages, layers, tables, or visible structure.
3. Call `artifact_normalize` to create a `botji.artifact_schema.v1` contract before transformation.
   This applies to every file type: image, PDF, text, DXF/CAD, DOCX, XLSX, HTML, SVG, STEP, IFC, ZIP, audio, video, or binary fallback.
4. Create a source inventory before transformation. Split it into hard acceptance requirements and advisory preferences.
   Hard requirements are source facts or explicit user constraints; advisory preferences are style, finish, lighting, and best-effort exactness.
5. If the user asks for a derivative output, call `artifact_transform` with `source_artifact_ids`.
   Omit `operation` or use `operation: "exact_copy"` when the output must preserve the source byte-for-byte. This is the default and the 100% file-fidelity route.
   Prefer `provider_route: "openai_codex"` when the user wants to use Codex/ChatGPT subscription auth.
   Use `operation: "render_schema"` before image generation when exact structure matters.
6. Call `artifact_review` before presenting a generated artifact as faithful.
   Pass the hard acceptance requirements as `fidelity_requirements`; advisory preferences may warn but must not become blockers unless the user made them mandatory.
7. Final replies must name the output artifact ID/path and review ID/path when available.
   Return the artifact record `path` under `/opt/data/artifacts/outputs/...`, not a raw `/opt/data/cache/...` path from a generation tool. Cache paths are only staging inputs.

## Route rules

- Exact preservation: use `artifact_transform` with omitted operation or `operation: "exact_copy"`; this verifies byte-for-byte preservation and is not a mock.
- Source-image edit or render: use `artifact_transform(operation: "edit_image", provider_route: "openai_codex")` only when the contract has an explicit change list. Do not use prompt-only `image_generate`.
- Schema-first preview or intermediate: use `artifact_transform(operation: "render_schema")`. This route is deterministic, not a mock, and is the preferred bridge for any file type before a styled/rendered output.
- New concept image with no source file: `image_generate` is allowed only when the contract says concept generation.
- PDF questions: extract page/text/table evidence first. Do not claim OCR or exact table structure if the PDF has no text layer and OCR was not performed.
- DXF/CAD questions: use DXF structure as the geometry authority. Vision is only a preview/review signal.
- TXT/code/document questions: preserve line spans and cite extraction evidence.

## Fidelity claim levels

- `verified`: deterministic evidence exists, such as checksum, parser extraction, schema validation, diff, or test output.
- `reviewed`: model or human comparison was performed and persisted.
- `draft`: useful output exists but review is incomplete.
- `unverified`: no reliable evidence was extracted.

## 100% transform fidelity

For intentional transformations, 100% means transform-contract fidelity, not byte-exact file identity. Require:

- byte-exact baseline source copy,
- explicit allowed-change list,
- all hard source-preservation requirements pass,
- no partials, conflicts, blockers, or provider fallback,
- persisted `fidelity_scores.transform_contract_fidelity_percent: 100`.

Provider image edits can pass at 100% transform-contract fidelity while remaining `final_claim_level: reviewed`.

## No fallback

If `artifact_transform` returns `auth_required` or a provider error for source-bound image work, stop and report the failure. Do not silently switch to `image_generate`. A source-bound Codex route must pass the image as `input_image` and persist route evidence.

## Professional work

For architecture, interiors, cabinetry, product design, diagrams, construction, or CAD:

1. Separate source facts from inferred assumptions.
2. Ask for missing measurements when exactness matters.
3. Prefer DXF/PDF/vector/schema evidence over raster/vision evidence for dimensions.
4. Treat visual image output as reviewed, not verified, unless deterministic geometry checks were run.
   For 100% preservation, prefer exact_copy or schema/CAD/vector output over generative image output.
5. Preserve user vocabulary and project constraints in the contract.
6. For drawings with modules or bays, inventory the ordering, counts, appliance/object positions, labels, and forbidden inventions before generation.

---

## Photo Fidelity Mode (2D→3D transforms)

Use when source is a raster image (phone photo, render, screenshot, reference) — not a parseable CAD file.

### Choose the right mode

| Source type | Mode | Steps |
|---|---|---|
| Phone photo, render, screenshot, reference image | **Photo mode** | 4 steps |
| DXF, PDF, IFC, SVG with parseable geometry | **Technical mode** | 8 steps |
| User provides only dimensions, no drawing | **Spec mode** | 4 steps |
| User says "just generate" or waives fidelity | **Concept mode** | 2 steps |

### Photo mode pipeline (4 steps, ~60–90s)

Do NOT run `artifact_extract`, `artifact_normalize`, `schema_validate`, or `user_confirm` on a photo. Those steps are for technical drawings only.

```
1. artifact_register(path, role="source", declared_type="image")

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

3. artifact_review(source_artifact_ids=[source_id], output_artifact_id=output_id,
     fidelity_requirements=hard_preserve, use_openai_vision=True)

4. Deliver: send image first, then review badge
```

Use structured brief fields — NOT a freeform `instructions` string. The plugin assembles the final prompt from these fields.

**If user provides dimensions** ("Size: 2400mm × 1500mm"), add to `hard_preserve` — do NOT run schema_validate:
```
hard_preserve=["Overall dimensions: 2400mm wide × 1500mm tall — set scale and proportions from this", ...]
```

### Technical mode pipeline (8 steps)

Use when source has machine-readable geometry (DXF, PDF, IFC, SVG):

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

**Zone alignment rule:** For multi-zone layouts (upper/lower zones, floor plan + elevation), vertical element boundaries must align or the user must confirm: "The zone boundaries don't align. Top: [dims]. Bottom: [dims]. Intentional?"

### Spec mode (dimensions only, no drawing)

```
1. Build dimensional spec from user text (no artifact_register needed)
2. image_generate with visual brief including spec dimensions
3. Brief visual comparison against spec
4. Deliver with claim level "reviewed"
```

Set `schema_authority: user_provided`. Disclose dimensions are interpreted, not extracted.

### Concept mode (user waives fidelity)

Set `schema_authority: user_waived`, `final_claim_level: draft`. One step: `image_generate` with style brief.

### Photo mode fidelity review

| Review result | Action |
|---|---|
| `preserve_change: conflict` + objects added not in source | Block — regenerate with stricter FORBIDDEN list |
| `preserve_change: conflict` + layout/count wrong | Block — regenerate |
| `preserve_change: conflict` + minor proportion/style drift | **Warn only** — deliver with caveat |
| `preserve_change: partial` + style differences only | Pass — deliver with honest caption |
| `groundedness: conflict` due to invalid route | Fix route evidence, re-review |

Do not auto-block for minor style drift. Block when object count changes or source elements are invented or removed.

Photo mode claim level is always `reviewed`. Never `verified` for an image edit.

### Sketch-to-render review thresholds

When the source is a sketch, floor plan, or schematic (use `botji-2d-to-3d` skill for the full pipeline):

The review authority is the **spatial manifest** (element count, order, adjacency), NOT pixel geometry. A hand-drawn sketch will NEVER geometrically match a 3D photorealistic render — dimensional and proportion differences are expected.

| What changed | Action |
|---|---|
| Element count changed | Block |
| Element left-to-right order changed | Block |
| Adjacency violated (gap or filler inserted between adjacent elements) | Block |
| Object added or removed | Block |
| Proportions/depth/perspective differ from sketch | **Warn only — do not block** |
| Lighting, material, or finish drift | Pass |
| Sketch line details not replicated | Pass |

If `delivery_gate == "blocked"` and ALL conflicts are proportion/geometry drift (no count/order/adjacency violations), override the gate and deliver with a warn caveat. The gate is for spatial violations, not artistic interpretation differences.

### Delivery

Send image first, then:
```
✅ 3D render · route: edit_image · claim: reviewed · fidelity: [N]%
[1-line match/conflict summary]
```
