Compare the source image(s) and output image for source fidelity using only visible evidence.

Hard fidelity requirements:
{{REQUIREMENTS}}

## MODALITY RULE (apply first)

Determine the source image type before classifying conflicts:
- If the source is a hand-drawn sketch, floor plan, schematic, or 2D technical diagram AND the output is a 3D photorealistic render: this is a SKETCH-TO-RENDER transform.
- If the source is a photo, render, or CGI image: this is a SAME-MODALITY transform.

**Sketch-to-render rules:**
- Dimensional proportions, depth perspective, rendering style, and photographic finish differences are EXPECTED — classify as soft_conflicts.
- A 3D render will ALWAYS look proportionally different from a 2D sketch. This alone is NOT a hard conflict.
- Only flag as hard_conflict when: (a) a module or element is ADDED that is not in the sketch, (b) a module or element is REMOVED from the sketch, (c) the LEFT-TO-RIGHT MODULE ORDER is different from the sketch, or (d) two modules that appear directly adjacent in the sketch have a visible gap or extra cabinet between them in the output.

**Same-modality rules:** apply the full classification below.

## Conflict classification

- hard_conflicts: object/element ADDED that is not in the source, object REMOVED from source, count changed, LEFT-TO-RIGHT MODULE ORDER changed, directly-adjacent modules have a gap or extra element between them in the output, label or text wrong.
- soft_conflicts: proportion slightly off, minor position offset, material/color/finish different, lighting variation, texture change, style interpretation, depth/perspective differences — only when no hard requirement is violated. For sketch-to-render, geometric proportion differences always go here.

Return JSON only:
{"verdict":"pass|warn|block","matches":[],"partials":[],"hard_conflicts":[],"soft_conflicts":[],"unknowns":[],"required_corrections":[]}
verdict=block when any hard_conflict exists.
verdict=warn when only soft_conflicts or partials, no hard_conflicts.
verdict=pass when no conflicts.
If a requirement starts with 'Allowed transform:', it is NOT a conflict.
