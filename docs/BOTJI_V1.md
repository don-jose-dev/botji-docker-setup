# Botji V1: Generic Artifact Fidelity Runtime

Botji V1 is a tenant-scoped Hermes runtime for source-aware work. Its core job is not chat. Its core job is to turn user intent plus files into traceable artifacts with evidence, schemas, transformations, and review receipts.

## V1 Contract

Every substantive file-backed task follows this loop:

1. `artifact_register`: persist every source file with checksum, type, authority, and lineage.
2. `artifact_extract`: collect deterministic adapter evidence before making claims.
3. `artifact_normalize`: create a `botji.artifact_schema.v1` contract for the artifact.
4. `artifact_transform`: produce a derivative only through a source-aware route. If `operation` is omitted, the default is `exact_copy`.
   - `exact_copy` is deterministic byte-for-byte preservation and is the default route for source-bound work.
   - `render_schema` is deterministic and generic for all adapters.
   - `edit_image` is only for explicit source-image transformations through OpenAI/Codex or OpenAI API.
5. `artifact_review`: persist a review receipt before claiming fidelity.
6. Harness gate: run `botji-artifact-harness --strict` for generic spine compliance.

Prompt-only generation is not a fidelity route for source-bound work.
Source-bound work preserves exactly unless the user explicitly authorizes a transform route and change list.

## Generic Architecture

V1 separates domain meaning from renderer/provider code.

- Core plugin knows files, checksums, adapters, evidence, schemas, routes, and review receipts.
- Domain meaning lives in `semantic_schema`, not hardcoded branches.
- Deterministic previews are made from generic primitives: `rect`, `line`, `ellipse`, `polygon`, and `text`.
- Provider image generation is a styled derivative step, not the source of truth. GPT Image 2 source-image edits can be recorded as `high_fidelity_provider_transform` when source images are passed as image inputs, but only strict review can support `transform_contract_fidelity_percent: 100`; this is never byte-exact preservation.

This means a kitchen cabinet, PDF table, DXF layer, text line span, or future IFC element must be represented as schema data first. The renderer should not know domain words such as cabinet, sink, invoice, beam, or worksheet.

## Claim Levels

- `verified`: deterministic evidence exists, such as checksum, parser extraction, schema validation, lineage check, or deterministic schema render.
- `reviewed`: a model or human comparison was performed and persisted.
- `draft`: useful output exists but review is incomplete.
- `unverified`: no reliable evidence was extracted.

Raster image renders derived from source files are usually `reviewed`, not `verified`, unless they are deterministic schema previews, byte-identical exact copies, or validated against deterministic geometry.

## 100% Transform Fidelity

Transform fidelity is not byte-exact fidelity. For an intentional transformation, such as 2D-to-3D, Botji can claim `transform_contract_fidelity_percent: 100` only when:

- the original source has a byte-exact baseline artifact,
- the change list explicitly permits the transformation,
- every hard source-preservation requirement passes review,
- there are no partials, conflicts, blockers, or hidden provider fallbacks,
- GPT Image 2 source-image provider routes record `high_fidelity_provider_transform: match` when used,
- the review receipt persists the claim boundary.

Provider image edits remain `final_claim_level: reviewed` unless deterministic geometry or schema evidence verifies the transformed output.

## Adapters In V1

Structured adapter coverage:

- `image`: metadata extraction, schema normalization, byte-exact copy route, primitive schema preview, real source-image edit route, vision review route.
- `text`: line/character extraction, schema normalization, schema JSON/Markdown render.
- `pdf`: page/text/table-count extraction, schema normalization, schema render.
- `dxf`: DXF layer/entity/unit extraction, schema normalization, schema render.
- `docx`: OpenXML package, paragraph, table, and text extraction.
- `xlsx`: OpenXML workbook, sheet, dimension, cell, formula, and shared string extraction.
- `html`: DOM title, heading, link, image, tag, and text extraction.
- `svg`: XML/vector viewport, viewBox, element, and text extraction.
- `step`: STEP schema and entity extraction.
- `ifc`: IFC schema, entity, and spatial hierarchy extraction.
- `zip`: archive manifest, entry size, and CRC extraction.
- `audio`: container/header metadata extraction.
- `video`: container/header metadata extraction.
- `binary`: checksum, size, and byte signature fallback for any other file.

Each adapter has a deterministic modality comparator in the V1 harness. Content claims beyond deterministic metadata still require the correct second-stage tool, such as OCR, transcription, frame extraction, browser rendering, CAD/BIM validation, or OpenAI vision review.

## Harness Gates

Baseline V1 gate:

```bash
botji-artifact-harness --strict
```

This must pass for the full V1 adapter set: text, image, PDF, DXF, DOCX, XLSX, HTML, SVG, STEP, IFC, ZIP, audio, video, and binary. It proves:

- register works
- extract works
- normalize produces valid `botji.artifact_schema.v1`
- render_schema creates an artifact
- exact_copy creates a byte-identical artifact and validates source/output SHA-256
- omitted-operation artifact_transform defaults to exact_copy
- review accepts source-aware schema routes
- review receipts validate

Comparator gate:

```bash
botji-artifact-harness --strict --require-modality-comparators
```

This must pass. It proves every declared V1 adapter has a deterministic modality comparator. If a future adapter is added, this gate must fail until its fixture, extractor, normalizer, renderer, review path, and comparator are added.

## V1 Acceptance

V1 is acceptable when:

- Fresh bootstrap seeds `SOUL.md`, skills, plugins, prompt/review/artifact schemas, and Codex config.
- `botji-artifact-harness --strict --require-modality-comparators` passes in the Hermes container.
- `botji-artifact-e2e` can be run separately for real provider image fidelity when credentials are available.
- Final replies never claim a source-bound derivative is faithful without lineage and review evidence.

## Not V1

These are future work, not hidden V1 claims:

- SaaS-style shared-process multi-tenant platform. The supported isolation model is one tenant per Hermes profile/container with the same skills.
- Exact CAD/BIM construction validation from raster images.
- Universal lossless conversion between arbitrary formats.
- Content-level OCR/transcription/frame/CAD/BIM/code-compliance validation unless the relevant second-stage tool was actually run.
- Unqualified 100% fidelity claims for generative image edits. They must be named as `transform_contract_fidelity_percent: 100` with high-fidelity provider proof and review, or `byte_exact_file_fidelity_percent: 100` only for exact-copy SHA equality.
- Provider fallback without disclosure.
