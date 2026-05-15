# Visual Artifact Fidelity Prompt Template

Use for image-to-image, 2D-to-3D, CAD-to-render, diagram, layout, and design tasks.
Do not use generic adjectives. Write every prompt as a photography or rendering brief.

---

## Brief structure

```
CAMERA: [body e.g. Sony A7 IV | Canon EOS R5] · [lens e.g. 24mm tilt-shift | 85mm f/1.8] · [view e.g. straight-on front elevation | 3/4 perspective]
LIGHT: [quality e.g. soft diffused | hard direct] · [direction e.g. front-left, 45°] · [source e.g. overcast window | studio softbox | golden hour]
MOOD: [genre e.g. architectural interior photography | commercial product | editorial showroom | technical illustration]
COLOR: [palette e.g. warm white, warm oak, matte black | cool grey, brushed steel]

SUBJECT:
[Precise description. Use counts, left-to-right order, module widths, appliance names. No prose — use a list.]

HARD PRESERVE (source constraints — do not alter):
- [item 1]
- [item 2]

ALLOWED CHANGES:
- [item 1]

FORBIDDEN:
- [anything that must not appear or change]
```

---

## Checklist before sending prompt to provider

- [ ] Camera + lens specified (not "3D render" or "photorealistic")
- [ ] Lighting quality, direction, and source named
- [ ] Mood genre named as a photography/rendering style
- [ ] Subject uses exact counts and positions from schema
- [ ] Hard preserve list copied from normalized schema
- [ ] Forbidden list includes: extra objects, merged modules, changed positions, added clutter
- [ ] Source image passed as pixel input (not described in text) when route is `image_edit`

---

## Post-generation comparison

After generation, run this comparison against the source:

```
Match:    [what the output got right]
Partial:  [what drifted but is within tolerance]
Conflict: [what contradicts the source schema]
Unknown:  [what could not be verified visually]
```

Final claim: `draft` | `reviewed` | `verified`
(reviewed = Botji compared output; verified = deterministic check ran)

---

## Example: kitchen front elevation

```
CAMERA: 24mm tilt-shift · straight-on front elevation · centered on full kitchen run · slight perspective to show countertop depth only
LIGHT: soft diffused overcast · front-left 30° · studio fill no hard shadows · 5500K white balance
MOOD: architectural interior photography · editorial showroom · clean uncluttered

SUBJECT (left to right, hard module order):
1. 610mm full-height tall pantry/integrated appliance
2. 813mm sink base — inset undermount sink, pull-down faucet
3. 813mm drawer base — two wide horizontal drawer fronts
4. 813mm cooktop base — induction hob flush on countertop
5. 610mm appliance tower — compact microwave above, oven below
6. 610mm full-height tall fridge/pantry

Upper wall zone (left side only):
- 3 × 610mm flat-front wall cabinets, shallow 305mm depth
- 1219mm open hood zone — centered chimney extractor hood
- Remaining right side: open wall

HARD PRESERVE:
- Module count: exactly 6 floor modules, exactly 3 upper cabinets
- Module order: as listed above, no rearrangement
- Module widths: 610/813/813/813/610/610 bottom, 610/610/610/1219 upper
- Hood centered above cooktop module 4
- Continuous countertop, recessed grey toe-kick

ALLOWED CHANGES:
- Cabinet finish: warm white matte slab fronts
- Handles: slim brushed metal horizontal bar
- Appliances: stainless steel + black glass
- Floor: pale natural stone tile
- Wall: off-white neutral

FORBIDDEN:
- Do not add island, bar stools, dining table, plants, people, decorative objects
- Do not merge, add, remove, or reorder modules
- Do not change appliance positions
- Do not add windows or doors visible behind run
- Do not use corner or L-shape layout
```
