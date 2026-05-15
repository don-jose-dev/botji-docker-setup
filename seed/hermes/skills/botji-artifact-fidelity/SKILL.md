# Botji Artifact Fidelity Skill

Use this skill whenever the user provides or references a file, image, PDF, text document, DXF/CAD drawing, generated output, or any derivative that must stay faithful to a source.

## Core rule

Files are evidence, not decoration. Register the file as an artifact before analysis, extraction, transformation, or final claims.
The default transformation policy is exact preservation: if the user provided a source file, preserve it byte-for-byte unless the user explicitly authorizes a transform route and change list.

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
- Source-image edit or render: use `artifact_transform(operation: "edit_image", provider_route: "openai_codex")` only when the contract has an explicit change list; use `provider_route: "openai_api"` for OpenAI API-key billing. Do not use prompt-only `image_generate`.
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
