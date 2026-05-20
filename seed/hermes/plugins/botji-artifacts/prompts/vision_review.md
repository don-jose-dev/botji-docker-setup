Compare the source image(s) and output image for source fidelity using only visible evidence.

Hard fidelity requirements:
{{REQUIREMENTS}}

Requested / allowed transform brief:
{{TRANSFORM_BRIEF}}

Treat explicit user-requested additions, removals, moves, and replacements in
the transform brief as allowed changes. Do not classify those requested changes
as hard conflicts against the original source. Still block source drift outside
the requested transform and still enforce the hard fidelity requirements above.

## MODALITY RULE (apply first)

Determine the source image type before classifying conflicts:
- If the source is a hand-drawn sketch, floor plan, schematic, or 2D technical diagram AND the output is a 3D photorealistic render: this is a SKETCH-TO-RENDER transform.
- If the source is a photo, render, or CGI image: this is a SAME-MODALITY transform.

**Sketch-to-render rules:**
- Dimensional proportions, depth perspective, rendering style, and photographic finish differences are EXPECTED — classify as soft_conflicts.
- A 3D render will ALWAYS look proportionally different from a 2D sketch. This alone is NOT a hard conflict.

**HARD conflicts (block-worthy) for sketch-to-render** — only these four, nothing else:
1. A specific named element/object is ADDED that is not in the sketch (e.g. "added an extra shelf not shown in the source", "added a pendant light not in the sketch").
2. A specific named element/object is REMOVED from the sketch (e.g. "the left wall shelf is missing", "the window seat is gone").
3. The LEFT-TO-RIGHT ELEMENT ORDER on a SINGLE WALL or ZONE is changed (e.g. "on the right wall, the desk and the wardrobe are swapped").
4. Two elements that share a wall/zone and were drawn touching now have a REAL FILLER, PANEL, or EXTRA ELEMENT between them in the output (e.g. "a shelf is inserted between the desk and the wardrobe on the right wall").

**Kitchen / elevation major-inventory rule:** if the source or hard requirements
include any of these major items, you must explicitly verify each one in
`matches` or list it in `hard_conflicts`: extractor/range hood, refrigerator,
sink/faucet, island, stool count, pendant count, oven stack, cooktop/range. A
missing major item is always a hard conflict even for sketch-to-render.

**NOT hard conflicts (these are soft, do not block):**
- Open floor space, walking space, or clearance between a central element and a perimeter wall. Open-plan layouts are SUPPOSED to have clearance between zones and perimeter — this is architectural, not a violation.
- "Appears repositioned", "appears expanded", "appears shifted" — these describe proportional drift, not real reorder.
- Adjacency that does not match "exactly" — exact adjacency is impossible across the modality jump from 2D sketch to 3D render.
- Material, finish, lighting, perspective, or style interpretation differences.
- Element detail, hardware style, texture, or color interpretation.

The key test for a hard conflict: can you point to a SPECIFIC NAMED OBJECT that is in the output but not in the source (added), or in the source but not in the output (removed), or on a different wall/zone than the source places it (reordered)? If yes, it's hard. If you're describing proportions, spacing, depth, or "appears X", it's soft.

**Same-modality rules:** apply the full classification below.

## Conflict classification

- hard_conflicts: object/element ADDED that is not in the source, object REMOVED from source, count changed, LEFT-TO-RIGHT ELEMENT ORDER changed, directly-adjacent elements have a gap or extra element between them in the output, label or text wrong.
- soft_conflicts: proportion slightly off, minor position offset, material/color/finish different, lighting variation, texture change, style interpretation, depth/perspective differences — only when no hard requirement is violated. For sketch-to-render, geometric proportion differences always go here.

Return JSON only:
{"verdict":"pass|warn|block","matches":[],"partials":[],"hard_conflicts":[],"soft_conflicts":[],"unknowns":[],"required_corrections":[]}
verdict=block when any hard_conflict exists.
verdict=warn when only soft_conflicts or partials, no hard_conflicts.
verdict=pass when no conflicts.
If a requirement starts with 'Allowed transform:', it is NOT a conflict.
