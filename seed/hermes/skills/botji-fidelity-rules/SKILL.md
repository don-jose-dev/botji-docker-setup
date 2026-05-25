---
name: botji-fidelity-rules
version: 1.0.0
description: Hard fidelity rules for artifact transforms, image generation, and review. Always active when artifact tools are in use.
requires_tools: []
---

# Fidelity Rules

## Transform rules

| Rule | Applies to |
|---|---|
| **Photo/reference → 3D: use 4-step fast path.** Do NOT run `evidence_extract`, `schema_validate`, or `user_confirm` on raster images. | Photo→3D transforms |
| Register source artifact before any extraction or transform. | All file/image work |
| Schema-first pipeline (8 steps): extract → validate → normalize → transform. | DXF, PDF, IFC, technical drawings ONLY |
| `image_generate` only when contract says `concept_generation` or user waives fidelity. | New concepts only |
| If route fails, stop and disclose. Never silently downgrade to `image_generate`. | All transforms |
| Upper/lower layout zone boundaries must align or user must confirm mismatch. | Cabinetry, plans, elevations |
| `verified` only after an actual check ran. Otherwise `reviewed` or `draft`. | All claims |
| Deliver artifacts from `/opt/data/artifacts/outputs/…`, not cache paths. | All generated files |
| `transform_contract_fidelity_percent: 100` is valid after a persisted review — never implies byte-exact. | Image edits |

## Lineage chain

Every generated artifact must trace:
`source artifact → prompt contract → output artifact → review`

## Image generation rules

For every `operation_run(operation="edit_image")`, the brief MUST be structured (not freeform prose):

- Use structured fields: `camera_brief`, `light_brief`, `mood_brief`, `subject_inventory`, `hard_preserve`, `forbidden_elements`
- NEVER use "photorealistic", "high quality", "8K", or "hyperrealistic" — these are noise. Use camera body + lens + lighting instead.
- FORBIDDEN list must explicitly name what gpt-image-2 is likely to hallucinate for the scene type (plants, clutter, extra panels, extra objects, people, furniture).

## Review blocking rules

Block before final send if ANY of these are true:

| # | Condition |
|---|---|
| 1 | Hard constraint violated |
| 2 | Unsupported claim stated as fact |
| 3 | `verified` claimed without an actual verification step |
| 4 | Source authority reversed |
| 5 | Destructive/high-stakes action taken without approval |
| 6 | Preserve list changed silently |
| 7 | Provider/model/tool fallback happened silently |
| 8 | Artifact lineage missing for a generated artifact |
| 9 | Generated artifact called faithful without a comparison step |
| 10 | Required review axis omitted |
| 11 | Upper/lower layout zone boundaries misaligned without user confirmation |
| 12 | Required route (`image_edit`, `exact_copy`) failed and was replaced by text-to-image |
