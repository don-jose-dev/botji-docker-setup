# Visual Artifact Fidelity Prompt Template

Use for all image-to-image, 2D-to-3D, reference-to-render, and design transform tasks.
Write every prompt as a photography or rendering brief. Never use generic adjectives.

---

## Required brief structure

```
CAMERA: [body e.g. Sony A7 IV | Canon EOS R5] · [lens e.g. 24mm tilt-shift | 35mm f/2.8] · [view e.g. front elevation | 3/4 perspective | top-down]
LIGHT: [quality e.g. soft diffused | hard direct] · [direction e.g. front-left 30°] · [source e.g. overcast window | studio softbox | golden hour · 5500K]
MOOD: [genre e.g. architectural interior photography | commercial product | editorial showroom | landscape photography | technical illustration]

SUBJECT (inventory from source — left-to-right, top-to-bottom):
  - [element 1: count, position, material if known]
  - [element 2]

HARD PRESERVE (source constraints — never alter):
  - [structural elements and layout]
  - Object count: exactly [N] [elements]
  - [spatial relationships]

ALLOWED CHANGES:
  - [explicit list of what can change]

FORBIDDEN (always list these explicitly):
  - Do not add any objects not visible in the source image
  - Do not add [specific: plants / furniture / people / extra panels / decorative objects]
  - Do not remove or reorder [specific elements]
  - Do not extend any element beyond its source boundary
```

---

## Checklist before sending to provider

- [ ] Camera body + lens specified (not "3D render" or "photorealistic")
- [ ] Lighting quality, direction, and color temp named
- [ ] Mood/genre named as a photography or rendering style
- [ ] SUBJECT uses counts and positions from source (not prose)
- [ ] HARD PRESERVE includes object count and layout rules
- [ ] FORBIDDEN names what gpt-image-2 is likely to hallucinate for this scene type
- [ ] Source image passed as pixel input, not just described in text

---

## Post-generation comparison

After generation, compare against source:

```
Match:    [what the output got right]
Partial:  [what drifted within tolerance]
Conflict: [what contradicts the source — list specifically]
Unknown:  [what could not be verified visually]
```

Final claim: `reviewed` (Botji compared) | `verified` (deterministic check ran)
Photo→3D transforms are always `reviewed`, never `verified`.

---

## Examples by use case

### Vertical garden / green wall (photo→3D)

```
CAMERA: Canon EOS R5 · 35mm f/4 · straight-on front elevation · level horizon
LIGHT: soft diffused natural · overhead front · 5500K daylight
MOOD: architectural landscape photography · editorial showroom · clean background
SUBJECT (left to right, top to bottom):
  - Full-wall vertical garden panel, 2400mm wide × 1500mm tall
  - 6 horizontal planter rows, staggered foliage, dense green fill
  - Wall-mounted aluminium frame, visible edges
HARD PRESERVE:
  - Wall span: full width edge-to-edge, no gaps at sides
  - Row count: exactly 6 horizontal rows
  - Overall dimensions: 2400mm wide × 1500mm tall
  - Frame structure visible at edges
ALLOWED CHANGES:
  - Convert flat 2D appearance to 3D depth and volume
  - Plant species variation (natural mix)
  - Lighting shadows for 3D render
FORBIDDEN:
  - Do not add pots, urns, tables, chairs, people, or floor plants
  - Do not add decorative lighting, signage, or branding
  - Do not add structural elements beyond the frame
  - Do not crop or cut off the wall edges
```

### TV feature wall / entertainment unit (photo→3D)

```
CAMERA: Sony A7 IV · 24mm tilt-shift · front elevation · eye level
LIGHT: warm ambient interior · front-left 30° · 3200K warm white
MOOD: architectural interior photography · editorial residential · warm showroom
SUBJECT (left to right):
  - Left: plain taupe wall panel
  - Centre-left: fluted vertical panel, brass trim
  - Centre: TV niche, TV mounted flush
  - Right: display shelving with warm-lit shelves
  - Floating console below fluted + TV zone only, taupe fronts with underlighting
HARD PRESERVE:
  - Module order: plain panel · fluted panel · TV zone · display shelving
  - Console stops before right shelving — does NOT extend under it
  - TV centred in niche
  - Display shelving is a distinct right module, separated from console
ALLOWED CHANGES:
  - Material finishes (taupe, brass, walnut palette)
  - Lighting warmth and ambience
FORBIDDEN:
  - Do not extend console under the right display shelving
  - Do not merge console and shelving into one continuous base
  - Do not add floor plants or objects not in source
  - Do not reorder the four main modules
```

### Kitchen elevation (photo→3D)

```
CAMERA: 24mm tilt-shift · straight-on front elevation · centred on full kitchen run
LIGHT: soft diffused overcast · front-left 30° · studio fill · 5500K
MOOD: architectural interior photography · editorial showroom · clean uncluttered
SUBJECT (left to right, hard module order):
  - [Module 1: type, width]
  - [Module 2: type, width]
HARD PRESERVE:
  - Module count: exactly [N] floor modules
  - Module order: as listed, no rearrangement
  - Continuous countertop
ALLOWED CHANGES:
  - Cabinet finish (match source palette)
FORBIDDEN:
  - Do not add island, bar stools, dining table, plants, people
  - Do not merge, add, remove, or reorder modules
  - Do not change appliance positions
```

### Landscape / garden design (photo→3D)

```
CAMERA: Sony A7 IV · 28mm · eye-level perspective · slight elevation
LIGHT: natural overcast diffused · mid-morning · 6000K cool daylight
MOOD: landscape architecture photography · residential garden · editorial
SUBJECT:
  - [Garden elements from source with positions and counts]
HARD PRESERVE:
  - Layout zones from source (hardscape / planting / water feature positions)
  - Object count: [N] primary elements
ALLOWED CHANGES:
  - Render plants in 3D volume, natural shadows
FORBIDDEN:
  - Do not add structures, furniture, or people not in source
  - Do not add trees or shrubs not visible in source
```

### Technical drawing → 3D model

```
CAMERA: [view matching drawing projection]
LIGHT: studio neutral, no harsh shadows
MOOD: technical product render · engineering visualization
SUBJECT: [from schema, module by module with dimensions]
HARD PRESERVE:
  - All schema dimensions: [list]
  - Module count: exactly [N]
  - All object positions from schema
ALLOWED CHANGES:
  - Materials as annotated or neutrally inferred
FORBIDDEN:
  - Do not deviate from schema dimensions
  - Do not add elements not in the schema
```
