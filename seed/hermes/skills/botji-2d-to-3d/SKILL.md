---
name: botji-2d-to-3d
version: 3.1.0
description: Convert 2D sketches, floor plans, or hand-drawn layouts to 3D renders using a spatial-manifest pipeline. Required when the source is a sketch, not a photo.
tags: [botji, 2d-to-3d, sketch-to-render, interior, design, fidelity, spatial-manifest]
---

# 2D → 3D Render (Spatial-Manifest Pipeline)

Use this skill when the source is a **sketch, floor plan, hand-drawn layout, or printed schematic** and the user wants a 3D render. Fidelity standard: **spatial manifest** (element count + order + adjacency), not pixel geometry.

Works for any domain: kitchens, wardrobes, living rooms, retail displays, garden layouts, product arrangements, etc.

---

## When to use

| Source | Skill |
|---|---|
| Hand-drawn sketch | **This skill** |
| Floor plan or elevation | **This skill** |
| Printed schematic | **This skill** |
| Phone photo of a real space | botji-artifact-fidelity Photo mode |
| Reference render or CGI | botji-artifact-fidelity Photo mode |
| DXF/CAD file | botji-artifact-fidelity Technical mode |

---

## Step 0 — Extract the spatial manifest (ALWAYS do this before any tool call)

Inspect the source sketch visually. Write a spatial manifest before calling any tool.

```
SPATIAL MANIFEST — [scene type, e.g. kitchen / wardrobe / living room]

LEFT WALL (left → right as drawn):
  Pos 1: [type/label]
  Pos 2: [type/label]
  ...

RIGHT WALL (left → right as drawn):
  Pos 1: [nearest room center]
  Pos 2: [middle]
  Pos 3: [outer edge]
  ...

BACK WALL (if visible):
  Pos 1: [type/label]
  ...

CENTRAL ELEMENT / ISLAND (if present):
  [type, orientation, approx. dimensions]

TOTAL ELEMENT COUNT: [N]

CRITICAL ADJACENCY CONSTRAINTS:
  — [Pos X Wall Y] is DIRECTLY adjacent to [Pos Z Wall Y] — NO filler, panel, or gap between them
  — [Element] terminates at [point] — does NOT continue further

OPENING CONSTRAINTS:
  — [Room/zone] [door/entry/window] is on the [wall/side] at [left/center/right/near corner]
  — [Door] swing/handing: [visible source swing/handing, or unknown if not legible]
```

Verify the manifest against the sketch. If a label is ambiguous, write both interpretations.

---

## Step 1 — Register source + extract manifest

```python
# Register
source = artifact_register(path=..., role="source", declared_type="image")

# Extract manifest automatically (preferred over writing it by hand)
manifest_result = evidence_extract_manifest(artifact_id=source["artifact_id"])
manifest = manifest_result["manifest"]
fidelity_reqs = manifest_result["fidelity_requirements"]
# manifest contains: scene_type, source_modality, elements[], element_count,
# adjacency_constraints[], opening_constraints[], layout_hints[]
```

If `evidence_extract_manifest` fails (Codex unavailable), fall back to writing the manifest manually as shown in Step 0.

> **Future shape (Phase C2 onward — not yet emitted).** A typed v2 manifest schema is now scaffolded at `seed/hermes/schemas/manifest_v2.schema.json` with Pydantic models alongside. v2 will replace prose labels with controlled-vocab `type` + `zone` fields so review compares typed rows by `(element_id, type, zone)` instead of fuzzy-matching prose. The pipeline still emits v1 today — **do not hand-write v2 manifests in this skill yet**. See [`docs/MANIFEST_V2.md`](../../../../docs/MANIFEST_V2.md) for the migration plan.

---

## Step 2 — Build the manifest-driven prompt

Translate the spatial manifest into the `operation_run` call. Every constraint is derived from the manifest. The FORBIDDEN list must name elements **explicitly** — generic "don't reorder" is not enough.

```python
operation_run(
    operation="edit_image",
    source_artifact_ids=[source_id],
    contract_id=contract_id,

    camera_brief="24mm tilt-shift · straight-on front elevation · centred · eye level",
    light_brief="soft diffused overcast · front-left 30° · studio fill · 5500K daylight",
    mood_brief="architectural interior photography · editorial showroom · clean",

    subject_inventory=[
        # List elements in strict LEFT-TO-RIGHT order, one entry per wall/zone
        "LEFT WALL (L→R): [Pos1 label] — [Pos2 label] — [Pos3 label]",
        "RIGHT WALL (L→R from room center): [Pos1 label] — [Pos2 label] — [Pos3 label]",
        # repeat for other walls/zones
    ],

    hard_preserve=[
        "Exact left-to-right element order per wall: must match subject_inventory sequence",
        "Total element count: exactly [N] units",
        # One entry per adjacency constraint from manifest:
        "[Wall, Pos X] [label] is DIRECTLY adjacent to [Wall, Pos Y] [label] — ZERO space between them",
        "[Room/zone] [door/entry/opening] stays on the [wall/side] at [position] with the same visible swing/handing",
        # repeat per constraint
    ],

    forbidden_elements=[
        # SPECIFIC adjacency violations first (name the exact elements):
        "DO NOT place any filler, panel, or empty space between [label A] and [label B] on the [wall/zone name]",
        "Do NOT move [room/zone] [door/entry/opening] from the [source wall/side] to any other wall",
        "Do NOT change the visible swing/handing of [room/zone] door",
        # repeat per adjacency constraint
        # Generic scene hallucinations (always include):
        "Do NOT reorder or swap any element from its drawn position",
        "Do NOT add any element or object not in the source sketch",
        "Do NOT add decorative countertop/surface objects not shown in source",
        "Do NOT add plants, decor, artwork, or people",
        "Do NOT expand any central element beyond its sketched footprint",
        "Do NOT add lighting fixtures not shown in source",
        "Do NOT extend any run or wall beyond its drawn endpoint",
    ]
)
```

### Writing specific FORBIDDEN entries from the manifest

For each adjacency constraint in the manifest, write a named FORBIDDEN entry:

| Manifest constraint | FORBIDDEN entry |
|---|---|
| Element A (pos 2) adj. to Element B (pos 3), right wall | `"DO NOT place any filler or panel between [Element A] and [Element B] on the right wall. They are directly adjacent — zero gap."` |
| Left tower immediately adjacent to centre unit | `"DO NOT add any element between the left tower and the centre unit. They share a wall face."` |
| Island terminates before right base run | `"DO NOT extend the island to touch or overlap the right base run."` |

---

## Step 3 — Review against the spatial manifest

```python
review_record(
    source_artifact_ids=[source_id],
    output_artifact_id=output_id,
    # Use fidelity_reqs from evidence_extract_manifest — or build manually from Step 0
    fidelity_requirements=fidelity_reqs or [
        "Total element count: exactly [N] units",
        "[Adjacency constraint verbatim from manifest]",
    ],
    use_openai_vision=True
)
```

---

## Step 4 — Apply modality-aware verdict

For sketch-to-render, the review authority is the **spatial manifest**. Apply these thresholds:

| What the output changed | Verdict |
|---|---|
| Element count changed | **BLOCK** |
| Element left-to-right order changed | **BLOCK** |
| Adjacency constraint violated (gap or filler inserted) | **BLOCK** |
| Object added or removed | **BLOCK** |
| Proportion or depth differs from sketch | **WARN only** |
| Lighting, material, finish drift | **PASS** |
| Sketch line details not replicated | **PASS** |

If `delivery_gate == "blocked"` AND the ONLY conflicts are proportion/geometry drift (not count/order/adjacency), override and deliver with a caveat:
> "The element order is preserved. Proportions differ from the sketch — expected when converting a hand drawing to a 3D render. [specific drift] Accept this result?"

---

## Step 5 — Retry strategy (on block)

The canonical budget is **1 attempt + at most 1 retry per turn** — defined in `botji-render-mode` (Retry budget). The retry combines BOTH escalations below into a single call (don't burn the budget on two cosmetic re-runs).

### Orchestration: prefer the Kanban workflow template

The durable orchestration for this retry lives at `kanban/workflows/render_retry.yaml` (seeded into `$HERMES_HOME/kanban/workflows/`). It encodes the same `1 + 1` shape as a 7-step Kanban task: `step_extract_manifest → step_transform_initial → step_review_initial → step_retry_transform → step_review_retry → step_deliver | step_ask_user`. Each step carries `current_step_key`, so the chain survives container restarts and session compaction.

**When to use Kanban vs inline retry:**

| Flow shape | Use Kanban template | Inline (this prose) |
|---|---|---|
| Source-bound render with manifest (this skill's core path) | **PREFER** when the template is on disk and Kanban dispatch is enabled | Fallback only |
| Casual / spec-mode flows in `botji-render-mode` | — | **OK** — no source to preserve, no manifest, single shot |
| Concept mode (waived fidelity) | — | **OK** |

**How to check:** if `$HERMES_HOME/kanban/workflows/render_retry.yaml` is present AND `kanban.dispatch_in_gateway` is `true` in `config.yaml` (the default), prefer the Kanban template. Otherwise run the same retry inline using the prose below. Skill semantics are identical either way — the template is the durable layer; this prose is the always-on fallback.

### The retry call (template step `step_retry_transform`, or inline)

Add the exact `primary_blocker` text from the review as the FIRST FORBIDDEN entry, AND move the adjacency constraint to the very first item in `subject_inventory`. Both moves in one transform:

```python
operation_run(
    ...
    prior_blocker=review["primary_blocker"],      # verbatim from review verdict
    retry_guidance=review.get("retry_guidance", ""),
    forbidden_elements=[...original list...],
    subject_inventory=[
        f"CRITICAL SPATIAL CONSTRAINT (most important): {adjacency_constraint}",
        # ... rest of subject list
    ],
)
```

### If the retry also blocks on the same constraint (template step `step_ask_user`)

Stop and ask:
> "I've made 2 attempts. The closest result I could produce conflicts on: [primary_blocker]. gpt-image-2 is having difficulty with this specific constraint. Options: (1) Accept this result with the noted conflict, (2) Adjust the sketch to be less ambiguous about this constraint, (3) Try a different camera angle."

Never silently run a third transform. If the user explicitly authorises another attempt, disclose the budget burn ("3rd attempt as requested — usual cap is 2") and proceed.

---

## Delivery

Image first, then badge:
```
✅ 3D render · route: edit_image · claim: reviewed
Spatial manifest: [N] elements · left: [summary] · right: [summary]
```

If warn: `⚠️ 3D render · claim: reviewed · proportion drift: [description] · element order: preserved`
If blocked: `❌ Blocked · conflict: [primary_blocker] · [retry guidance or options]`

---

## Common hallucination patterns by domain

Always include relevant FORBIDDEN entries for the scene type:

### Kitchens
| gpt-image-2 pattern | FORBIDDEN entry |
|---|---|
| Extra cabinet between appliance towers | `"DO NOT place any cabinet between [tower A] and [tower B]"` |
| Appliance positions swapped | `"DO NOT swap [element A] and [element B] positions"` |
| Island expanded | `"DO NOT expand island footprint beyond the sketch"` |
| Countertop objects | `"Do NOT add bowls, fruit, vases, utensils, or countertop appliances"` |
| Bar stools added | `"Do NOT add stools unless explicitly shown in source"` |
| Pendant lights added | `"Do NOT add pendant lights not shown in source"` |

### Wardrobes / fitted furniture
| Pattern | FORBIDDEN entry |
|---|---|
| Extra shelf or hanging rail added | `"Do NOT add shelves or hanging rails not in source"` |
| Drawer units swapped with door units | `"Do NOT swap drawer modules with door modules"` |
| Filler panel inserted between units | `"Do NOT insert filler between [unit A] and [unit B]"` |

### Living rooms / product arrangements
| Pattern | FORBIDDEN entry |
|---|---|
| Extra decorative objects | `"Do NOT add cushions, books, plants, or decor not in source"` |
| Furniture repositioned | `"Do NOT move [sofa/table/unit] from its drawn position"` |
| Additional lighting | `"Do NOT add floor lamps or ceiling fixtures not shown"` |
