# Typed Spatial Manifest v2

The typed spatial manifest is the foundation for the **typed-certainty rewrite**
of the Botji review pipeline. Today's pipeline (manifest v1) stores prose
labels and fuzzy-matches them against vision output; v2 stores controlled-vocab
typed rows that review compares row-by-row by `(element_id, type, zone)`.

This document describes v2. **v2 is scaffold only as of Phase C1.** Nothing in
the runtime pipeline emits or consumes v2 yet. C2 will change
`artifact_extract_manifest` to emit typed rows; C3 will change `artifact_review`
to consume typed alignment.

---

## Why v2

The v1 manifest contains entries like:

```json
{
  "id": "elem_3",
  "label": "base cabinets and countertop",
  "wall_or_zone": "back_wall",
  "position_index": 2
}
```

Review compares the prose `label` against the vision model's prose description
of the output. When the model writes `base cabinet run` or `cabinetry with
worktop` for the same drawn element, fuzzy matching silently passes (if it
overlaps) or blocks (if it doesn't), and the gate decision is downstream of
lexical taste rather than spatial truth.

v2 replaces the prose label with two typed fields:

```json
{
  "element_id": "countertop_01",
  "type": "countertop",
  "zone": "back_wall",
  "required": true,
  "status": "visible",
  "evidence": "continuous horizontal worktop above base run",
  "position": {"order_in_zone": 2}
}
```

Review now compares `(element_id, type, zone)` typed triples. Prose lives in
`evidence`, but it does not drive the gate.

---

## Schema

JSON Schema: [`seed/hermes/schemas/manifest_v2.schema.json`](../seed/hermes/schemas/manifest_v2.schema.json)
Pydantic models: [`seed/hermes/plugins/botji-artifacts/_manifest_v2.py`](../seed/hermes/plugins/botji-artifacts/_manifest_v2.py)
Converters: [`seed/hermes/plugins/botji-artifacts/_manifest_v2_compat.py`](../seed/hermes/plugins/botji-artifacts/_manifest_v2_compat.py)

### Top-level `Manifest`

| Field | Type | Required | Notes |
|---|---|---|---|
| `version` | const `"v2"` | yes | Distinguishes from v1 (no `version` field). |
| `scene_type` | string | yes | One of the v1 scene enums (kitchen, wardrobe, etc.). |
| `source_modality` | enum | yes | `sketch`, `floor_plan`, `photo`, `render`, `schematic`. |
| `elements` | `Element[]` | yes | Typed rows; one per discrete object/zone. |
| `adjacency_constraints` | `AdjacencyConstraint[]` | no | Pairs that must touch (or not). |
| `opening_constraints` | `OpeningConstraint[]` | no | Doors / windows / entry openings. |
| `layout_hints` | `string[]` | no | Free-form layout descriptors (U-shape, etc.). |
| `fidelity_requirements` | `string[]` | no | Carried through from v1 — pre-built review strings. |

`additionalProperties: false` on all nested objects so typos are caught at
validation time, not at review.

### `Element`

| Field | Type | Required | Notes |
|---|---|---|---|
| `element_id` | string, kebab snake_case | yes | Stable id like `countertop_01`. Validated by regex. |
| `type` | `ElementType` enum | yes | Controlled vocab. See below. |
| `zone` | `Zone` enum | yes | Controlled vocab. See below. |
| `required` | bool | yes | `true` for any element that must appear in a faithful render. |
| `status` | `visible` / `inferred` / `absent` | yes | Confidence band. |
| `evidence` | string ≤300 chars | no | Brief justification; holds v1 prose when status=inferred. |
| `position.order_in_zone` | int ≥1 | no | 1-based L→R order within the zone. |

### Controlled vocabs

`ElementType` covers kitchen, wardrobe, and living-room domains; see the schema
`$defs/ElementType` enum for the full list. Use `other` as the safety valve;
review treats `other` elements as informational only.

`Zone` enumerates: `left_wall`, `right_wall`, `back_wall`, `front_wall`,
`floor`, `ceiling`, `center`, `island`.

`Status`:

| Status | Meaning |
|---|---|
| `visible` | Drawn explicitly in the source; review must check. |
| `inferred` | Parsed from prose with low confidence; review may warn. |
| `absent` | Required but missing from the source; review must block if found in the render. |

---

## Migration plan

| Phase | What changes | Status |
|---|---|---|
| **C1** | Add v2 schema, Pydantic models, back-compat converters, fixtures. No runtime change. | **this PR** |
| **C2** | `artifact_extract_manifest` emits typed v2 rows. The adapter layer down-converts to v1 prose for the still-unchanged consumer. | future |
| **C3** | `artifact_review` consumes typed alignment directly. Adapter layer is retired. | future |
| **C4** | v1 prose path removed. v2 is the only manifest shape. | future |

The `prose_to_typed` / `typed_to_prose` converters in `_manifest_v2_compat.py`
make the staged migration possible — each side flips independently.

---

## Round-trip guarantees

Structural fields are preserved across `v1 → v2 → v1`:

- `scene_type` (verbatim)
- `source_modality` (verbatim)
- element count
- per-element `wall_or_zone` (when the v1 string was already a Zone enum value)
- per-element `position_index`
- `adjacency_constraints` count
- `opening_constraints` count
- `layout_hints` (verbatim)
- `fidelity_requirements` (verbatim)

What does NOT round-trip verbatim:

- Element `label` becomes either the controlled-vocab `type` (when classified)
  or the original prose string (when inferred). On the v2→v1 trip the label is
  reconstructed from `evidence` or the type slug, so it is recognizable but
  not byte-identical.
- `wall_or_zone` strings outside the controlled `Zone` enum (e.g. legacy
  `"centre"`) are normalized to canonical values (e.g. `"center"`).

This is acceptable because review never compared labels byte-for-byte — it
fuzzy-matched. Once the consumer is v2-aware in C3, prose drift cannot
silently pass review.

---

## When to use v2 vs v1

**Do not manually write v2 manifests in skill prose yet.** Until C2 lands, the
runtime `artifact_extract_manifest` emits v1 and `artifact_review` consumes
v1. Use v1 as shown in `seed/hermes/skills/botji-2d-to-3d/SKILL.md` Step 2.

The v2 module is exposed only for:

- Harness fixtures (`tests/fixtures/manifest/`)
- Internal validation (the Draft 2020-12 validator in `_handlers.py`)
- Forward-compatible consumers that already opt in (none in C1)

When C2 ships, this section will flip — at that point the typed manifest
becomes the canonical shape, and v1 becomes legacy.
