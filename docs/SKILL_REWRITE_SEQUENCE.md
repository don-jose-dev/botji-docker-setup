# Skill Rewrite — Post-V1R PR Sequence

Six PRs that move Botji's skill surface from technical names to pain-facing
names and add the five missing wedge skills identified in
[`PAIN_MAP.md`](PAIN_MAP.md).

**Not on the V1R critical path.** V1R PRs 8–11 must land first
([`BOTJI_V1R.md`](BOTJI_V1R.md)). Once V1R is complete and `botji-artifacts/`
is gone, this sequence is unblocked.

## Discipline (inherits from V1R)

1. **One PR per session.** No parallel rewrite PRs.
2. **Net LOC delta ≤ 0** for PRs that modify existing files. New skill PRs
   that only add files are exempt — but flag in the PR description.
3. **VPS smoke after every merge.** Both tenants (botji + degain) must come
   up green.
4. **No new paid services.** Codex subscription only. SAM 3.1 self-host
   sidecar is OSS / self-managed; not a vendor.
5. **CONTRIBUTING.md SKILL.md frontmatter** required on every new skill.

## The six PRs

### PR #1 — `botji-bounded-revision` skill (this PR)

**Pain absorbed:** #1 (scope/revisions) + #7 (client communication) + part of
#19 (accepted-output pricing).

**Why first:** Highest-frequency Reddit pain. Pure prose skill — no plugin
code, no provider deps. Forward-compatible with V1R in flight (does not
touch `botji-artifacts/` or any migrating plugin).

**Adds:**
- `seed/hermes/skills/botji-bounded-revision/SKILL.md`
- `seed/hermes/skills/botji-bounded-revision/references/revision_policy.yaml`
- `seed/hermes/skills/botji-bounded-revision/prompts/revision_round.md`

**Touches existing:** None in PR #1. PR #2 will wire it into
`botji-render-router`.

**Done:** new skill files exist, SKILL.md frontmatter passes the pre-commit
hook, `make smoke-local` green, both tenants come up healthy after deploy.

**Kill:** if the skill prose tries to enforce a revision count via code
instead of brief language, redesign — substrate stays mechanical, skills
own policy.

---

### PR #2 — Skill rename / merge pass

**Pain absorbed:** rename surface — pains #3, #4, #6, #11 keep their
absorbers but become discoverable by pain-facing names.

**Why second:** Cheap text edits, no plugin code. Catches the rewrite up to
the [`PAIN_MAP.md`](PAIN_MAP.md) naming before any new wedge skills land.

**Renames:**
- `botji-source-fidelity` → `botji-photo-locked`
- `botji-delivery-receipt` → `botji-client-handoff`
- `botji-artifact-fidelity` + `botji-fidelity-rules` → merge into
  `botji-drawing-fidelity`
- `botji-2d-to-3d` + `botji-cad-elevation` → merge into
  `botji-sketch-to-render`
- `botji-render-router` + `botji-render-mode` → consolidate into `botji-router`

**Move out of skills:**
- `botji-source-current` → fold into `botji-core/` (mechanical lookup, not a
  user-facing skill)
- `botji-codex-engineering` → `optional-skills/` (internal dev skill)

**Wires PR #1:** `botji-router` references `botji-bounded-revision` in the
route table.

**Done:** every renamed skill resolves under its new name; old paths exist
as one-release aliases (per V1R PR 10 alias pattern) with kill markers; pain
map table updated in same PR.

**Kill:** any skill rename without an alias for the previous release. Operators
upgrading must not see broken routes.

---

### PR #3 — `botji-budget-tier` skill

**Pain absorbed:** #2 (budget blowouts).

**Why third:** Pure prose skill. Forces the brief to declare a $/sqft tier
(good/better/best) and tags the receipt with the chosen tier. No plugin
code; references `botji-premium-brief` vocabulary.

**Adds:**
- `seed/hermes/skills/botji-budget-tier/SKILL.md`
- `seed/hermes/skills/botji-budget-tier/references/budget_tiers.yaml`

**Touches existing:**
- `seed/hermes/skills/botji-premium-brief/SKILL.md` — point at the tier
  reference

---

### PR #4 — `botji-targeted-edit` skill + `botji-segment` plugin

**Pain absorbed:** #13 (targeted edits without cascade) + #15
(existing-room preservation). This is the wedge.

**Why fourth:** Requires the SAM 3.1 sidecar GPU compose service.
Architectural change beyond skill text. Gated on V1R PR 11 (drop
`botji-artifacts/`) being done so `botji-render` is the only image-edit
substrate.

**Adds:**
- `seed/hermes/plugins/botji-segment/` (plugin.yaml + `__init__.py` + config)
- `seed/hermes/skills/botji-targeted-edit/SKILL.md` + references + prompts
- `seed/hermes/schemas/targeted_edit.schema.json`
- Compose: `docker-compose.sam.yml` (optional GPU sidecar profile)
- `.env.example` additions for `BOTJI_SEGMENT_ENDPOINT`,
  `BOTJI_SEGMENT_TIMEOUT_SECONDS`

**Touches existing:**
- `seed/hermes/plugins/botji-render/providers/openai_codex.py` — exercise
  `images.edit` with mask param
- `botji-router` — add targeted-edit route

**Done:** `/edit` skill flow works end-to-end on both tenants. Mask hash
recorded in receipt.

**Kill:** if the segmentation client embeds SAM weights into the main image,
move them to a sidecar. License forbids redistribution.

---

### PR #5 — `botji-revision-replay` skill + `botji-recipe` plugin

**Pain absorbed:** #14 (revision reproducibility).

**Why fifth:** Plugin work. The recipe schema and store are the moat
artifact. Gated on PR #4 because mask hash is part of the recipe.

**Adds:**
- `seed/hermes/plugins/botji-recipe/` (plugin.yaml + `__init__.py` +
  `recipe.schema.json`)
- `seed/hermes/skills/botji-revision-replay/SKILL.md` + references
- Recipe quintuple stored on every accepted render:
  `{prompt_hash, seed, model_version, source_image_hash, mask_hash,
   parent_recipe_id}`

**Touches existing:**
- `botji-render/operations.py` — write recipe on every output
- `botji-receipt` — embed `recipe_id` in receipt metadata
- `botji-router` — add replay route

**Done:** "same kitchen, change the countertop to walnut" routes through
`botji-revision-replay` against the prior recipe.

---

### PR #6 — `botji-audit-export` skill + receipt v2

**Pain absorbed:** #18 (underwriter-ready audit).

**Why sixth:** The sales artifact. Requires receipt v2 with proper
governance fields and per-tenant signing key. Final V1 build.

**Adds:**
- `seed/hermes/skills/botji-audit-export/SKILL.md` + references
- Receipt v2 fields in `botji-receipt`:
  - `botji.human_review` (license #, decision, timestamp)
  - `c2pa.ingredient` + attestation (model + version)
  - `cawg.training-mining` (training data class)
  - `c2pa.actions` with mask hash refs
- Per-tenant signing key (self-signed cert in tenant volume; OpenBao deferred)
- Postgres append-only `audit_ledger` table per tenant
- Monthly export tool: dump tenant's ledger as one signed JSON + PDF

**Touches existing:**
- `botji-receipt/__init__.py` — wire v2 fields
- `docker-compose.yml` — add `postgres-audit` service (OSS, in-compose)

**Done:** `/audit` command on Telegram emits a signed monthly export for the
tenant. Underwriter handoff is a single file.

## Out of this rewrite (V1.1+)

- Floor plan / spatial-extraction workflow (`spatial_extract.yaml` — second
  workflow; locked May 23 decision)
- Workflow YAML language abstraction (Phase 3 of
  [`HARNESS_LONG_TERM_PLAN.md`](HARNESS_LONG_TERM_PLAN.md))
- Scene set / multi-view consistency (`botji-scene-set`)
- Grafana + Alertmanager observability stack (Phase 4)
- restic DR + idempotency + tool allowlists (Phase 5)
- Mini App (mask canvas, receipt viewer)
- FF&E adapters (DesignFiles, Programa, Studio Designer)
- Per-firm LoRA training

## How this doc is enforced

- Every rewrite PR title starts with `rewrite/PR-N — <skill or change>` and
  references this file.
- The PR description includes the LOC delta line and the pain # absorbed
  from [`PAIN_MAP.md`](PAIN_MAP.md).
- Done / Kill criteria are merge gates.
- No two rewrite PRs open at once.

## Cross-references

- [`PAIN_MAP.md`](PAIN_MAP.md) — pain clusters → skills
- [`BOTJI_V1R.md`](BOTJI_V1R.md) — plumbing rewrite (this rewrite waits on
  V1R completion)
- [`BOTJI_HERMES_NATIVE_FIDELITY_FIRST_PLAN.md`](BOTJI_HERMES_NATIVE_FIDELITY_FIRST_PLAN.md)
  — phase plan
- [`HARNESS_LONG_TERM_PLAN.md`](HARNESS_LONG_TERM_PLAN.md) — long-term plan
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — frontmatter rules, sync workflow
