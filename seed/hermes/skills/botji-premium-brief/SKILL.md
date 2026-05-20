---
name: botji-premium-brief
description: Premium image-brief construction. Required reading before every artifact_transform(operation="edit_image") call. Defines CAMERA · LIGHT · MATERIALS · MOOD · REFERENCE · SIGNATURE structure plus banned noise vocabulary and worked examples. Without this discipline, output is flat AI-default.
tags: [botji, premium, image, brief, render]
version: 1.0.0
---

# Premium image-brief construction

The fidelity skills tell you what to PRESERVE. **This skill tells you what to SPECIFY** so the output is premium, not flat AI-default.

A brief without explicit MATERIAL · LIGHT · REFERENCE produces mediocre output. Every artifact_transform brief must include all three plus a SIGNATURE detail.

---

## Required brief structure

```
CAMERA · <lens (mm) · view · framing>
LIGHT · <Kelvin temperature · direction · quality>
MATERIALS · <named material with finish, per major surface>
MOOD · <one editorial phrase — never generic luxury adjectives>
REFERENCE · <one specific genre or publication>
SIGNATURE · <one quality of light or surface — never a new object>

SUBJECT:
  - <element 1>
  - <element 2>
HARD PRESERVE:
  - <source fact>
FORBIDDEN:
  - <known hallucination>
```

CAMERA · LIGHT · MATERIALS · MOOD · REFERENCE · SIGNATURE come **first** — they set the look. SUBJECT · HARD PRESERVE · FORBIDDEN follow — they set the fidelity.

---

## Vocabulary discipline

### MATERIALS — name with finish, never bare type

| Don't say | Say |
|---|---|
| wood | rift-sawn white oak with hand-rubbed oil finish |
| metal | brushed bronze with subtle patina |
| stone | honed Carrara marble with grey veining |
| concrete | cast concrete with visible formwork lines |
| fabric | natural linen with visible weave |
| leather | full-grain saddle leather with light wear |
| glass | low-iron glass with hand-polished edges |
| floor | wide-plank European oak, herringbone, matte oil |
| paint | lime-wash plaster, soft warm white |

Every major surface (floor, walls, primary furniture, hardware) gets a named material with a finish, a grain direction, or a wear state. One per surface — not a list of options.

### LIGHT — Kelvin + direction + quality

| Don't say | Say |
|---|---|
| good lighting | 3200K soft diffused from a north window, gentle wrap |
| warm lighting | 2700K warm rake light from the right, single source |
| bright | 4500K overcast daylight from skylight, even ambient |
| dramatic | 3500K hard directional from a single side window |
| evening glow | 2400K low warm rake from a single floor lamp |

Always specify: colour temperature in Kelvin, direction (named source), and one quality word.

### REFERENCE — genre or publication, never generic adjective

| Don't say | Say |
|---|---|
| realistic | Dezeen editorial residential photography |
| photorealistic | Architectural Digest residential feature |
| high quality | Apple Studio product photography |
| beautiful | Kinfolk lifestyle editorial |
| luxury | Norm Architects residential |
| modern | RIBA Journal contemporary residential |
| nice | Wallpaper* magazine architecture feature |

One reference per brief. Anchors the visual language.

### SIGNATURE — quality of light or surface, never an object

| Don't say | Say |
|---|---|
| add a vase | a soft caustic from the window catching the floor near the bed |
| add candles | a precise specular highlight on the polished brass tap |
| more decor | a soft falloff in the corner shadow under the cabinet |
| add plants | a gentle bounce light reflecting off the marble counter |

The signature is something the **light or material does**, never a new object (an added object is a fidelity violation).

---

## Banned noise vocabulary

These words tell the model nothing useful and produce generic AI-luxury output. **Never write them in a brief.** If you find any in your draft, rewrite with specifics.

- `realistic`, `photorealistic`, `hyperrealistic`
- `high quality`, `8K`, `4K`, `ultra HD`, `HDR`
- `beautiful`, `nice`, `gorgeous`, `stunning`, `amazing`
- `modern style`, `luxury`, `elegant`, `polished`, `refined`, `sleek`, `sophisticated`
- `good lighting`, `warm tones`, `well-lit`
- `cosy`, `inviting`, `dreamy`, `magical`
- `editorial-quality` (just say `editorial`)

---

## Worked examples

### Kitchen interior (3D from sketch)

```
CAMERA · 24mm full-frame interior · eye-level · slight 2-point perspective
LIGHT · 3000K warm rake from the right window · soft secondary fill from skylight
MATERIALS · floor: wide-plank European oak, herringbone, matte oil ·
  counters: honed Carrara marble with grey veining ·
  cabinets: rift-sawn white oak with hand-rubbed oil finish ·
  hardware: brushed bronze with subtle patina ·
  range hood: hand-formed brushed steel
MOOD · early morning, single coffee cup on the island
REFERENCE · Dezeen editorial residential
SIGNATURE · a soft caustic from the kitchen window catching the marble counter edge

SUBJECT:
  - left wall: tall pantry cabinet, full-height fridge column
  - back wall: range hood, six-burner cooktop, twin upper cabinets, oven tower
  - right wall: window, prep sink, base cabinets
  - centre: rectangular island with two seats
HARD PRESERVE:
  - U-shaped layout, island centred
  - exact six-burner range with overhead hood
  - left pantry full-height, no horizontal break
FORBIDDEN:
  - extra cabinets between cooktop and oven tower
  - additional pendants over the island
  - bar stool count change (must be 2)
  - extra appliances on the counter
```

### Bedroom interior (photo upgrade)

```
CAMERA · 28mm full-frame interior · eye-level · centred 1-point perspective
LIGHT · 2700K warm bedside lamp + 4200K cool indirect cove · evening
MATERIALS · floor: wide-plank European oak, matte oil ·
  bed frame: dark-stained ash with visible grain ·
  bedding: natural linen with visible weave, warm white ·
  bedside table: solid travertine with honed top ·
  walls: lime-wash plaster, soft warm white
MOOD · settled, lived-in, quiet end of day
REFERENCE · Norm Architects residential editorial
SIGNATURE · a soft falloff from the bedside lamp catching the linen pillow edge

SUBJECT:
  - back wall centred: low platform bed, upholstered headboard panel
  - left bedside table with lamp
  - right bedside table with stack of 3 books
  - left wall: tall window with sheer linen curtain
HARD PRESERVE:
  - low platform bed with exact upholstered headboard
  - sheer curtain on the left window only
  - one lamp on each bedside
FORBIDDEN:
  - extra pillows beyond source
  - art on the back wall
  - additional plants
  - changed bedside layout
```

### TV / media wall

```
CAMERA · 35mm full-frame interior · eye-level · 1-point perspective on the wall
LIGHT · 3500K indirect ceiling cove · 2700K warm under-shelf accent strip
MATERIALS · wall: vertical fluted rift-sawn oak with hand-rubbed oil ·
  shelves: solid blackened steel with bronze fasteners ·
  console: hand-stained ash veneer over carcass, brass pulls ·
  floor: wide-plank European oak, matte oil
MOOD · evening living room, single overhead lamp on, TV off
REFERENCE · Architectural Digest residential feature
SIGNATURE · a soft warm under-shelf glow catching the fluted oak grain

SUBJECT:
  - left wall: built-in cabinet tower
  - back wall: 3 floating shelves stepped vertically
  - centre wall: wall-mounted TV above low console
  - right of console: single potted plant
HARD PRESERVE:
  - exactly 3 floating shelves
  - centred wall-mounted TV
  - one potted plant
FORBIDDEN:
  - extra shelves
  - additional plants
  - rearranged element order
  - changed TV placement
```

---

## Process

1. Read the source image.
2. Inventory elements → SUBJECT list (fidelity).
3. Identify user-requested changes → HARD PRESERVE vs FORBIDDEN (fidelity).
4. **Now add premium discipline** — fill the CAMERA · LIGHT · MATERIALS · MOOD · REFERENCE · SIGNATURE block with specifics from the vocabulary tables above.
5. Run the brief through the **noise-vocabulary check**: search your draft for every banned word. If any is present, rewrite.
6. Verify at minimum:
   - LIGHT has a Kelvin number
   - MATERIALS names at least 3 surfaces with finish
   - REFERENCE names one specific genre or publication
   - SIGNATURE describes a quality of light or surface, not an object
7. Call `artifact_transform(operation="edit_image", source_artifact_ids=[...])` with the premium fields filled explicitly:
   `camera_brief`, `light_brief`, `materials_brief`, `mood_brief`,
   `reference_brief`, `signature_brief`, then the fidelity lists
   `subject_inventory`, `hard_preserve`, and `forbidden_elements`. Use
   `instructions` only for extra constraints that do not fit those fields.

---

## When to skip

Skip premium discipline only for:

- `operation="exact_copy"` (byte-identical output — no rendering)
- `fidelity_mode="schema_render"` (deterministic schema PNG — no aesthetic choices)
- The user explicitly waives premium ("just a quick draft", "concept only")

Everything else needs the full brief.

---

## Folded from botji-image-brief (consolidated 2026-05-19)

The former `botji-image-brief` skill was an alias-only stub pointing here. It has been removed; this skill (`botji-premium-brief`) is the single source for image-brief construction. If an older caller still references `botji-image-brief` by name, treat it as a synonym for `botji-premium-brief` and load this skill instead.

No unique rules or examples lived in the stub — the full brief format (CAMERA · LIGHT · MATERIALS · MOOD · REFERENCE · SIGNATURE), banned-noise vocabulary, and worked examples are all defined in the sections above.
