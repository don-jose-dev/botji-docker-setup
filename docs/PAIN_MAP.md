# Pain Map — Reddit/community pain clusters → Botji skills

Source clusters: r/InteriorDesign, r/smallbusiness, r/architecture practice
threads (May 2026 research pass). Each cluster names the highest-frequency
complaint and the Botji skill that absorbs it.

A pain cluster without a dedicated skill column means the workflow handles it
inside other skills (router, brief, contract) — not that it is unaddressed.
"GAP" means no skill currently absorbs that cluster and it is on the
[`SKILL_REWRITE_SEQUENCE.md`](SKILL_REWRITE_SEQUENCE.md) backlog.

## Cluster → skill table

| # | Pain cluster (Reddit signal) | Owning skill | Status |
|---|---|---|---|
| 1 | Scope & revisions — endless feedback loops, undefined "revision rounds" | `botji-bounded-revision` | NEW (PR #1 of rewrite) |
| 2 | Budget blowouts — design at $100k, contractor bid $330k | `botji-budget-tier` (forces $/sqft tier in brief) | GAP — PR #3 of rewrite |
| 3 | Deliverable quality — Pinterest boards vs room-specific renders | `botji-photo-locked` (renamed from `botji-source-fidelity`) | EXISTS — rename pending |
| 4 | Billing transparency — opaque hours, "what did I pay for?" | `botji-client-handoff` (renamed from `botji-delivery-receipt`) | EXISTS — rename pending |
| 5 | Procurement / FF&E — spreadsheets for 150+ SKUs | `botji-catalog-lock` + `botji-visual-inventory-review` | EXISTS |
| 6 | Toolchain friction — CAD→SketchUp→V-Ray crashes | `botji-router` + `botji-sketch-to-render` (merged from `botji-2d-to-3d` + `botji-cad-elevation`) | EXISTS — merge pending |
| 7 | Client communication — feedback from too many channels | Handled by `botji-bounded-revision` (one consolidated round) + Telegram thread model | NEW (folded into PR #1) |
| 8 | AI skepticism (r/InteriorDesign Rule 3) — bans AI renderings in pro posts | `botji-client-handoff` ships C2PA + provenance receipt | EXISTS via `botji-render` v1.1 C2PA |
| 9 | Free / devalued work — "design for exposure" requests | Positioning, not a skill. Reflected in V1 pricing posture (paid firm tool, not consumer ChatGPT). | Not a skill |
| 10 | Business development — B2B cold email failure | Not in V1 scope. Botji is delivery, not lead-gen. | Out of V1 |
| 11 | Drawing liability — no revision metadata, NKBA clearance disputes | `botji-drawing-fidelity` (merged from `botji-artifact-fidelity` + `botji-fidelity-rules`) | EXISTS — merge pending |
| 12 | Privacy / confidentiality — client room photos to third-party AI | One-tenant-per-firm container model + explicit provider disclosure in receipt | EXISTS via deploy pattern |
| 13 | Targeted edits without cascade — "change only the countertop" | `botji-targeted-edit` (SAM 3.1 sidecar + `gpt-image-2 images.edit` mask) | GAP — PR #4 of rewrite |
| 14 | Revision reproducibility — "same kitchen next month, change one thing" | `botji-revision-replay` + `botji-recipe` plugin | GAP — PR #5 of rewrite |
| 15 | Existing-room preservation — preserve walls / openings / camera | Covered by `botji-photo-locked` + `botji-targeted-edit` | EXISTS / GAP |
| 16 | Buildability / code / MEP — pretty renders fail HSW | Skill copy claim-boundary only in V1 (`botji-prompt-contract`). Automated checks = V2+. | Skill copy only |
| 17 | BIM / Revit / IFC native intelligence | Adapter plugins only (`botji-adapters/`). Round-trip = V2+. | Adapter-level |
| 18 | Underwriter-ready audit — E&O carve-back (Verisk CG 40 47/48) | `botji-audit-export` + receipt v2 | GAP — PR #6 of rewrite |
| 19 | Accepted-output pricing — credit burn on rejected renders | `botji-bounded-revision` records billable round metadata on receipt; actual billing impl = V2 | Metadata only |
| 20 | Prompt-engineering tax — non-architects fight with prompts | `botji-premium-brief` enforces structured brief vocab | EXISTS |

## Six gaps prioritised (becomes [`SKILL_REWRITE_SEQUENCE.md`](SKILL_REWRITE_SEQUENCE.md))

1. **`botji-bounded-revision`** — pain #1 + #7 + part of #19. PR #1.
2. **Skill renames + merges** — pains #3, #4, #6, #11. PR #2 (text only).
3. **`botji-budget-tier`** — pain #2. PR #3.
4. **`botji-targeted-edit`** + `botji-segment` plugin — pain #13 + #15. PR #4.
5. **`botji-revision-replay`** + `botji-recipe` plugin — pain #14. PR #5.
6. **`botji-audit-export`** + receipt v2 — pain #18. PR #6.

## How this doc is enforced

- New skills land with a row in this table.
- Skill PRs reference the pain # they absorb.
- Renames update the "Owning skill" column in the same PR that ships the
  rename.
- If a Reddit/community signal arrives that doesn't fit a cluster, a new row
  goes here before any skill work begins.

## Cross-references

- [`BOTJI_V1.md`](BOTJI_V1.md) — product contract
- [`BOTJI_HERMES_NATIVE_FIDELITY_FIRST_PLAN.md`](BOTJI_HERMES_NATIVE_FIDELITY_FIRST_PLAN.md)
  — phase plan that hosts the rewrite
- [`SKILL_REWRITE_SEQUENCE.md`](SKILL_REWRITE_SEQUENCE.md) — PR ordering
- [`HARNESS_LONG_TERM_PLAN.md`](HARNESS_LONG_TERM_PLAN.md) — long-term phases
