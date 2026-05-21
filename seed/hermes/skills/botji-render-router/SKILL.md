---
name: botji-render-router
version: 1.0.0
description: Skill-first router for Botji image/render requests. Required before every "make 3D", render, edit, or source-bound image transformation. Chooses Photo, Sketch, Technical, Parallel, or Concept mode and enforces speed/retry budgets.
tags: [botji, render, routing, image, performance, fidelity, skill-first]
---

# Botji Render Router

Use this skill first for every image/render request, before reading `botji-2d-to-3d`,
`botji-artifact-fidelity`, `botji-premium-brief`, or `botji-parallel-render`.

The router owns the workflow decision. Plugins execute tools. Reviews enforce
delivery. Do not let a plugin capability decide the route by accident.

Current production image generation still runs through the legacy
`artifact_*` tools. For source-bound image work, use the legacy `art_*` image
pipeline end-to-end, then record the reviewed output with `receipt_record` and
`delivery_gate`. Do not start normal "make 3D" turns with `source_register` until
Hermes-native generation has replaced `artifact_transform`.

Do not mix identifier families:

- `src_*` IDs come from `botji-core` and are valid for `source_current`,
  `artifact_write`, `receipt_record`, and `delivery_gate`.
- `art_*` IDs come from legacy `artifact_*` tools and are valid for
  `artifact_extract_manifest`, `artifact_transform`, `artifact_review`, and
  compatibility `receipt_record` / `delivery_gate`.
- Never pass a `src_*` ID to a legacy `artifact_*` tool. If a legacy extractor is
  required, call `artifact_register` on the same current attachment path and use
  that returned `art_*` ID for the legacy leg.
- Do not repair a receipt failure with repeated retries. One ID-family repair is
  allowed; after that, deliver the reviewed output with a caveat or stop.

---

## Route Decision

Classify the source before calling tools:

| Source/request | Route | Skill to use next | Default budget |
|---|---|---|---|
| Phone photo, real room, screenshot, previous render, product reference | **Photo mode** | `botji-artifact-fidelity` + `botji-premium-brief` | 1 transform + 1 review |
| Hand sketch, floor plan, elevation, schematic, printed drawing | **Sketch mode** | `botji-2d-to-3d` + `botji-premium-brief` | 1 transform + 1 review; 1 retry only with clear blocker |
| DXF, IFC, STEP, SVG, PDF/vector drawing with extractable geometry | **Technical mode** | `botji-artifact-fidelity` Technical mode | Extract/normalize first |
| 2+ independent images or variants | **Parallel mode** | `botji-parallel-render` | Fan out, one render per branch |
| No source file, user asks for a fresh idea | **Concept mode** | `botji-artifact-fidelity` Concept mode | No review gate; claim `draft` |

If unsure between Photo and Sketch, choose Photo mode unless the source visibly
contains drawn lines, plan/elevation symbols, handwritten layout, dimension marks,
or schematic annotations.

---

## Fast Path For "Make 3D"

For a single uploaded photo/reference image and the text `Make 3d`:

1. Register the image attached in the **current user turn**:
   `artifact_register(path=<current attachment path>, role="source", declared_type="image")`
   Use the returned `art_*` ID as `source_id`.
2. Do not call `source_register` or `source_current` for this route.
3. Build a structured premium brief from visible facts and user memory. Fill
   `camera_brief`, `light_brief`, `materials_brief`, `mood_brief`,
   `reference_brief`, and `signature_brief`; do not rely on mood alone for the
   premium look.
4. `artifact_transform(operation="edit_image", source_artifact_ids=[source_id], ...)`
5. `artifact_review(source_artifact_ids=[source_id], output_artifact_id=<output art_*>, use_openai_vision=True)`
6. Call `receipt_record(source_ids=[source_id], output_id=<output art_*>, route="artifact_transform.edit_image", status=<pass|warn|block>, ...)`, then `delivery_gate`.
7. Deliver if `delivery_gate` is `clear` or `warned`; if blocked, show the blocker and ask whether to retry.

Do **not** call:

- `artifact_extract_manifest`
- `artifact_extract`
- `artifact_normalize`
- `session_search`
- `skill_view`

unless the user asked for technical measurement, a source is a sketch/schematic,
or you genuinely lack a required tool argument.

This is the normal route for photos and reference renders. It should complete in
roughly one provider image call plus one review call.

Never reuse an artifact ID from a previous turn just because it is in context.
If the user attached a file in the current turn, that exact attachment path (or
same SHA-256 bytes) must be the parent lineage for the output. A stale source ID
is a hard block.

---

## Sketch Route

Use `botji-2d-to-3d` only when the source is a sketch, floor plan, elevation, or
schematic.

Write the spatial manifest yourself first. Use `artifact_extract_manifest` only
when one of these is true:

- the sketch has more than 10 elements,
- two or more adjacency relationships are ambiguous,
- labels/dimensions are hard to read,
- the user explicitly asks for strict spatial extraction,
- the first review blocks on count/order/adjacency and the manual manifest needs audit.

Do not spend 60-90 seconds on manifest extraction for a simple visible photo or
clear 3-5 element sketch.

---

## Retry Budget

Retries are a user-experience budget, not a provider loop.

| Situation | Action |
|---|---|
| Review warns only | Deliver with caveat |
| Review blocks on style/proportion only | Deliver with caveat; do not retry automatically |
| Review blocks on source object count/order/adjacency | One automatic retry is allowed only if the correction is obvious |
| Second block on the same issue | Stop and ask user: accept, retry, or adjust source/brief |
| Provider/auth/tool error | Stop and report the failed route; no silent fallback |

Never run more than two `artifact_transform(operation="edit_image")` calls in one
user turn unless the user explicitly asked for multiple variants.

## Kitchen / Elevation Hard Blocks

For kitchen sketches, elevations, cutlists, and technical drawings, these are
major inventory requirements. Missing or unverified items block delivery unless
the user explicitly allowed removal:

- extractor/range hood
- refrigerator
- sink/faucet
- island
- stool count
- pendant count
- oven stack
- cooktop/range
- left-to-right major zone order

Mention every present major item in `fidelity_requirements` before review. Do
not downgrade a missing major item to a style/proportion warning.

---

## Multi-Tenant Rule

The skill pack is shared; profiles are isolated.

Each tenant gets the same `botji-*` skills and plugins, but separate:

- Hermes home (`HERMES_HOME`)
- Codex home (`CODEX_HOME`)
- Telegram bot token
- allowlist file
- sessions/logs/memory/artifacts
- workspace

Do not implement tenant branching inside skills. The active profile/container
selects the tenant; the skill behavior stays identical.

---

## Delivery Labels

Use one of these concise labels:

- `Photo mode · route: edit_image · claim: reviewed`
- `Sketch mode · manifest: manual|extracted · claim: reviewed`
- `Technical mode · schema: verified · claim: reviewed`
- `Parallel mode · N renders · M delivered`
- `Concept mode · claim: draft`

If a budget was intentionally exceeded, say why in one line.
