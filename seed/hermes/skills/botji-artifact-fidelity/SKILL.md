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

1. Call `artifact_register` for every source file path. Pass `current_turn_id` set to the same value you would use for `source_register` / `source_current` so the dedup short-circuit cannot return a stale lineage record from an earlier turn when the user re-sends identical bytes.
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

## Render-mode dispatch (when there is a source artifact)

When the user has provided a source image, drawing, or dimensions and wants a render, edit, or 3D output, switch to `botji-render-mode`. That skill owns the photo / technical / spec / concept mode selection, per-mode pipelines, photo-mode and sketch-mode review thresholds, and the delivery badge format. For sketch / floor plan sources, the full spatial-manifest pipeline lives in `botji-2d-to-3d`.

---

## CAD / Production drawings

For any "CAD", "DXF", "DWG", "production drawing", "shop drawing", "cutlist", or "fabrication sheet" request, switch to `botji-cad-elevation`. That skill owns the hard rules (no `edit_image` for CAD output, `ezdxf` via the hermes venv only, no raw DXF group codes), the full register → extract → vision → manifest → normalize → ezdxf → review pipeline, the minimum cabinet-elevation template, and the companion cutlist CSV contract.
