---
name: botji-catalog-lock
version: 1.0.0
description: Enforces per-tenant material catalog on every render brief. Required before every operation_run(operation="edit_image"). Rejects briefs with banned terms; substitutes generic terms with catalog values.
tags: [botji, fidelity, vocabulary, brand]
---

# Botji Catalog Lock

Every tenant has a brand vocabulary — the specific stones, woods, metals,
paints, and fixtures they actually use in deliverables. Generic terms
("wood", "marble", "metal") and noise words ("luxury", "modern", "sleek")
produce flat AI-default output that does not look like that tenant's work.
This skill locks the brief to the catalog **before** any render call.

## When to call `catalog_check`

Call `catalog_check(brief=<full brief text>)` before every
`operation_run(operation="edit_image")`. Includes briefs constructed by
`botji-premium-brief`, `botji-2d-to-3d`, and any other render-bound skill.

Skip only when: `operation="exact_copy"`, `fidelity_mode="schema_render"`,
or the user explicitly waives catalog lock for a quick concept draft.

## Verdicts

- **`pass`** — proceed directly to `operation_run`.
- **`warn`** — generic terms found with catalog substitutions (e.g. "wood"
  → "rift-sawn white oak"). Rewrite the brief using the suggested values,
  note the substitutions to the user in plain English, then proceed.
- **`block`** — at least one banned term. **Do not call `operation_run`.**
  Return the banned hits and suggested substitutions to the user, ask them
  to rewrite with specifics, then re-run `catalog_check` on the new draft.

## Adding to the catalog

`catalog_update(field=<stones|woods|metals|paints|fixtures>, value=[...])`
**only with explicit user instruction**. The user is the source of truth
for brand vocabulary; never invent new catalog entries.

## Worked example

User: *"render a kitchen with wood cabinets, a marble island top, and
brushed brass handles, modern style"*

1. Construct the brief via `botji-premium-brief`.
2. `catalog_check(brief=<draft>)` returns:
   `{"status": "block", "banned_hits": ["modern"],`
   `"suggested_substitutions": {"wood": "rift-sawn white oak",`
   `"marble": "Calacatta Nuvo"}, "catalog_terms_used": ["brushed brass"]}`.
3. Tell the user: *"'modern' is banned; I'll use 'rift-sawn white oak'
   for 'wood' and 'Calacatta Nuvo' for 'marble'. OK to proceed?"*
4. On confirmation, rewrite the brief and re-check. Expect `status: pass`.
5. Now call `operation_run(operation="edit_image", ...)`.

`botji-premium-brief` bans generic AI-luxury words; this skill enforces
**tenant-specific** vocabulary on top.
