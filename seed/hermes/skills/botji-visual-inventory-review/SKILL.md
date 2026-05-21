---
name: botji-visual-inventory-review
version: 0.1.0
description: Skill-owned semantic image review for Botji renders. Checks source inventory, premium quality, and blockers before a delivery receipt.
tags: [botji, hermes-native, review, inventory, premium, fidelity]
---

# Botji Visual Inventory Review

Use this skill after a source-bound render or edit and before
`botji-delivery-receipt`.

## Review Contract

Write checks as structured facts. Do not let the plugin decide semantics.

Each check must include:

- `name`
- `status`: `pass`, `warn`, or `block`
- `source_evidence`
- `output_evidence`
- `user_visible_note`

## Kitchen And Interior Major Inventory

For kitchens, cabinetry, elevations, cutlists, and interiors, missing or
unverified major source items block delivery unless the user explicitly allowed
the change:

- extractor/range hood
- refrigerator
- sink and faucet
- island
- stool count
- pendant count
- oven stack
- cooktop or range
- left-to-right major zone order
- explicit dimensions or cutlist rows when legible

Invented room context also blocks when the source is a flat elevation or
technical drawing: windows, people, plants, unrelated furniture, or surrounding
architecture not present in the source.

## Premium Quality

Premium quality is not a mood word. Check for concrete signals:

- coherent camera angle and believable perspective,
- controlled light direction and shadows,
- named materials with surface finish,
- refined edges, hardware, proportions, and joinery,
- no generic clutter or decorative objects unless present in source or requested,
- no washed-out low-contrast preview look.

Style drift warns. Missing source inventory blocks.

## Output

Create a compact `checks` list for `receipt_record`. If any check blocks, the
receipt status is `block`. If only premium/style checks warn, use `warn`.
Otherwise use `pass`.
