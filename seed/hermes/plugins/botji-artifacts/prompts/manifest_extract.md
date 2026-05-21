Inspect the source image and extract a precise spatial manifest.

Return JSON only — no prose. Identify every discrete named element visible in the image.

```json
{
  "scene_type": "kitchen_interior|wardrobe_interior|living_room|bathroom|bedroom|office|retail|exterior|product|generic_interior|unknown",
  "source_modality": "sketch|floor_plan|photo|render|schematic",
  "elements": [
    {
      "id": "elem_1",
      "label": "exact visible label or inferred name",
      "wall_or_zone": "left_wall|right_wall|back_wall|island|centre|freestanding",
      "position_index": 1,
      "notes": "any specific notes about this element"
    }
  ],
  "element_count": 0,
  "adjacency_constraints": [
    {
      "elem_a": "elem_1",
      "elem_b": "elem_2",
      "relation": "directly_adjacent|separated_by_gap|overlapping",
      "wall_or_zone": "left_wall"
    }
  ],
  "opening_constraints": [
    {
      "element_id": "elem_3",
      "room_or_zone": "maid room",
      "opening_type": "door|entry|window|arch|unknown",
      "wall_or_side": "bottom_wall|left_wall|right_wall|top_wall|back_wall|front_wall|unknown",
      "position_on_wall": "left side|center|right side|near corner|unknown",
      "swing_or_handing": "opens inward left-hand|opens inward right-hand|opens outward left-hand|opens outward right-hand|sliding|unknown",
      "notes": "visible source evidence for placement and swing"
    }
  ],
  "layout_hints": [],
  "fidelity_requirements": []
}
```

Rules:
- `elements` must list every discrete named object/unit/zone visible in the source, in left-to-right order per wall/zone.
- `position_index` is 1-based, left-to-right within the wall/zone.
- `adjacency_constraints` must include every pair of elements that are drawn TOUCHING with no gap between them.
- `opening_constraints` must include every door, entry opening, window, arch, and visible threshold. For each door/entry, capture the room or zone, exact wall/side, position along that wall, and swing/handing when visible.
- `layout_hints` should capture notable spatial facts: "U-shape layout", "island present", "3 walls of cabinetry", "open-plan", "single-wall run", etc.
- `fidelity_requirements` must be pre-built review strings ready for `artifact_review(fidelity_requirements=[...])`. Example: "Total element count: exactly 7 units", "Left wall (L→R): REF tower — 4-drawer base — oven stack", "REF tower is directly adjacent to 4-drawer base on left wall — zero gap", "Maid-room door remains on the bottom wall at the left side of the room with the same visible swing/handing".
- For sketch/floor_plan modality, note that proportions will differ in 3D renders — do NOT include proportion requirements.
- If an element label is ambiguous, use the most specific reasonable name (e.g. "tall larder unit" not "cabinet").
