# Botji Image Fidelity Skill

Use this skill when a user provides or references a source image and asks for a transformation, render, professional visualization, design revision, 3D version, restyle, cleanup, or any output where fidelity to the source matters.

## Core rule

Source pixels are evidence. Do not collapse them to prose and then call prompt-only generation faithful. When `artifact_*` tools are available, register, extract, normalize, and transform through the artifact layer first.
Preserve the source exactly by default through `artifact_transform(operation: "exact_copy")`. Provider image edits may preserve listed source constraints under an allowed-change contract, but they do not preserve source pixels byte-for-byte.

## Route selection

- Use `artifact_register` then `artifact_transform` with omitted operation, or `operation: "exact_copy"`, when the output must preserve the source image byte-for-byte. This is the default and the only 100% image file-fidelity route.
- Use `artifact_register` then `artifact_transform(operation: "edit_image")` only when the output must transform an uploaded/source image and the contract has an explicit change list while preserving layout, object identity, cabinetry, room plan, product geometry, diagram structure, or visual constraints.
- Use `artifact_normalize` and `artifact_transform(operation: "render_schema")` before image rendering when the source has modules, labels, dimensions, plan/elevation structure, or professional constraints.
- Use `image_edit` directly only as a compatibility fallback when the artifact plugin is unavailable.
- Use a schema-first route before rendering when the work depends on exact dimensions, cabinet modules, architectural plans, trade drawings, installation constraints, or professional measurements.
- Use `image_generate` only for new concepts, moodboards, loose ideation, or when the Prompt Contract explicitly says `visual_mode: concept_generation`.
- If `image_edit` is unavailable or returns `auth_required`, stop and state the missing capability. Do not silently switch to `image_generate` for fidelity work.
- For source-bound output delivery, never return a raw `/opt/data/cache/...` generation path. Register or transform the image and return the persisted `/opt/data/artifacts/outputs/...` artifact path.

## Required Prompt Contract fields

Add `visual_fidelity` to the contract:

```json
{
  "visual_fidelity": {
    "visual_mode": "source_edit",
    "source_image_paths": ["/opt/data/cache/images/source.jpg"],
    "selected_route": "exact_copy|image_edit|render_schema",
    "forbidden_routes": ["image_generate"],
    "geometry_authority": "source_pixels",
    "review_required": true
  }
}
```

## Fidelity prompt shape

When calling `image_edit`, the prompt must include:

- Source role: which image is the parent/reference.
- Preserve list: object counts, layout relationships, dimensions/text, labels, visible boundaries, proportions, and user-stated profession constraints.
- Change list: the allowed transformation.
- Assumption boundary: material, color, lighting, camera, missing dimensions, and styling assumptions.
- Forbidden drift: what must not move, disappear, be resized, or be invented as fact.
- Review request: compare the result against the source constraints before final reply.

Keep two lists, not one:

- Hard acceptance requirements: source facts and user-stated constraints that must pass review.
- Advisory preferences: visual polish, realism, exact styling, or best-effort details that can produce warnings unless the user explicitly made them mandatory.

## Professional fidelity

For cabinetry, interiors, construction, architecture, product design, diagrams, and other professional domains:

1. Extract a visible source inventory first.
2. Separate source facts from inferred geometry.
3. Ask for missing measurements when exactness matters.
4. Prefer deterministic schema/CAD/plan artifacts for dimensions.
5. Use `image_edit` for the visual render, not as proof of exact measurements.
6. Review the output as `reviewed`, not `verified`, unless a concrete measurement/test/schema validation was performed.
7. For 100% source preservation, use `exact_copy`; for plans, elevations, and cabinetry drawings, preserve module order, object positions, appliance counts, depth cues, and forbidden additions before optimizing for photorealism.

For 2D-to-3D or styled transforms, 100% transformation fidelity means every hard source-preservation requirement passed under the allowed-change list. Persist this as `transform_contract_fidelity_percent: 100`; when GPT Image 2 receives source images, also require `high_fidelity_provider_transform: match`. Do not call it byte-exact fidelity.

## Review gate

Before final answer:

- Confirm the tool route matched the contract.
- Confirm source images were passed as image inputs for fidelity transformations.
- Compare output against the preserve list.
- Mark `visual_route` and `source_geometry` as `match`, `partial`, `missing`, or `conflict`.
- If prompt-only `image_generate` was used for a source-image fidelity request, mark `visual_route: conflict` and regenerate or ask for correction.
- For multi-zone layout sources (cabinets, plans, elevations): check that upper and lower zone vertical boundaries align. If they differ and the user has not confirmed the misalignment is intentional, mark `layout_fidelity: conflict / blocking` and ask before delivering.

## 2D-to-3D work

When the user asks for a 3D render, model, or perspective view from a 2D source, use the `botji-2d-to-3d` skill. It defines the mandatory schema-first pipeline, zone alignment checks, route priority, fidelity scores, and blocking rules specific to that transform type. Do not generate 3D output from a text prompt alone when a 2D source artifact exists.
