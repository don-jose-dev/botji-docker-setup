# Hermes-Native Botji Rewrite Plan

Status: Phase 0 in progress
Date: 2026-05-20

## Why rewrite

Botji currently has more product behavior in imperative plugin code than in
Hermes skills:

- `botji-artifacts` + `botji-allowlist`: about 4,202 Python lines
- all `SKILL.md` files: about 1,246 lines
- ratio: about 3.37x more plugin code than skill code

That shape is backwards for Hermes. The plugin should provide a small,
deterministic substrate for state, safe files, checksums, and receipts. The
skills should own routing, render strategy, source interpretation, retry budget,
and review judgement.

The 2026-05-20 VPS deploy stabilized production with Tier 1 patches, but it did
not remove the large plugin surface.

## Current VPS fidelity findings

Recent live reviews show warnings rather than blocks, but the underlying issues
are stronger than the verdicts:

1. Existing Telegram sessions kept old context after the skill deploy.
   The latest sessions continued using previously loaded `botji-2d-to-3d` /
   `botji-artifact-fidelity` content and did not start from
   `botji-render-router`.

2. One Botji request attached a newer image path, but the render lineage used an
   older source artifact:
   - user turn referenced `/opt/data/image_cache/img_0638736de7ad.jpg`
   - output lineage used `art_20260520T064604Z_f2001290`
   - that artifact points to `/opt/data/image_cache/img_dcb054ae49ae.jpg`

   This is a hard fidelity failure. The gate should block any source-bound
   render when the output parents do not match the latest user-attached source
   for the current turn.

3. The latest kitchen elevation renders still drift visually:
   - one output omitted the central extractor hood shown in the source
   - outputs added surrounding room/window context not present in the elevation
   - proportions and dimensional relationships were treated as warnings even
     when the source was a cutlist/elevation with explicit dimensions

The rewrite must treat current-turn source lineage and major source object
inventory as hard delivery gates.

## Immediate production mitigation

Before the rewrite, apply these operational rules:

1. Rotate active Hermes sessions after skill deployments.
   Existing sessions can keep old skill text in context and skip new routers.
   Move old session files to a timestamped backup instead of deleting them.

2. Add a current-turn source guard.
   For any source-bound render, the source artifact path or original path must
   match the latest image/file attached in the current user turn. If it does
   not, block before transform or delivery.

3. Promote major inventory misses from warning to block.
   For kitchens/elevations, missing hood, oven stack, fridge, sink, island,
   stool count, pendant count, or left-to-right zone order is a blocker unless
   the user explicitly allowed removal.

4. Keep the one-transform budget.
   A second transform is allowed only for a concrete blocker. Stale-source or
   missing-major-object failures must stop and ask; they should not silently
   retry with the wrong source.

## Target architecture

### Keep in code

Create a thin `botji-core` plugin. Target size: 300-600 lines.

Required tools:

- `source_register(path, role, current_turn_id)`
  Copies the file into the tenant artifact store, records checksum, MIME,
  size, original path, and current turn.

- `source_current(required_type=None)`
  Returns the files attached in the current turn and their registered artifact
  IDs. This gives skills a stable way to avoid stale artifact IDs.

- `artifact_write(path, parents, kind, claim_level, metadata)`
  Safely records a generated output under the tenant artifact store and checks
  that parents exist.

- `receipt_record(source_ids, output_id, route, checks, claim_level)`
  Persists the model-authored review receipt as structured JSON.

- `delivery_gate(receipt_id)`
  Enforces mechanical rules only: current-turn lineage, parent existence,
  blocked receipt status, missing required fields, and safe output paths.

Keep `botji-allowlist` and `botji-gate`, but simplify `botji-gate` to check
receipt state and lineage, not perform semantic review.

### Move to skills

Move these out of `botji-artifacts`:

- source classification: Photo, Sketch, Technical, Parallel, Concept
- manual spatial manifest writing
- DXF/PDF/PIL/PyMuPDF/ezdxf usage through code execution where needed
- render brief construction
- retry decisions
- vision comparison interpretation
- source inventory and major-object blocker rules
- user-facing delivery caveats

The model already has vision and code execution. The skill layer should tell it
exactly what to inspect, what to preserve, what to forbid, and when to block.

## Skill packages

### `botji-render-router`

Owns route selection and budget:

- Photo mode: register current source, transform, review
- Sketch mode: manual manifest, transform, review
- Technical mode: extract deterministic facts with code, then transform/review
- Parallel mode: one independent branch per source/variant
- Concept mode: no source-fidelity claim

### `botji-source-current`

New skill. Required before any source-bound transform.

Rules:

- identify latest user-attached files in the current turn
- call `source_current` or `source_register`
- never reuse an artifact ID from an earlier turn unless user explicitly says
  "use the previous image"
- block if output parents do not match current-turn source IDs

### `botji-visual-inventory-review`

New skill. Owns semantic review.

For kitchen/elevation requests it must check:

- tall appliance stack count and order
- fridge presence and side
- sink presence and side
- cooktop and hood/extractor presence
- island presence, size relationship, and stool count
- pendant count
- major upper/lower cabinet zone order
- explicit dimensions/cutlist rows when legible
- invented room context such as windows, people, plants, or extra furniture

Missing major source objects block. Perspective/material drift warns unless the
user requested dimensional fidelity.

### `botji-delivery-receipt`

New skill. Converts the visual review into a structured receipt and calls
`receipt_record` / `delivery_gate`.

## Migration phases

### Phase 0: Production safety

Goal: stop the current fidelity misses without changing the whole plugin.

Tasks:

- rotate active sessions after skill deployments
- add current-turn source guard to the existing plugin or gate
- update review rules so missing hood/fridge/sink/island/stool/pendant count
  blocks
- add a regression fixture for the current kitchen elevation

Implementation notes:

- `botji-fidelity-guard-harness` covers stale current-turn source lineage and
  major kitchen inventory silence.
- `botji-artifacts/_guardrails.py` blocks reviews whose source artifacts do not
  match the latest user attachment by path or SHA-256.
- `botji-artifacts/_vision.py` now treats missing/unverified major kitchen items
  as blocking review failures.
- `scripts/vps-rotate-sessions.sh` archives live session files after skill
  routing changes so existing Telegram threads do not keep stale skill text.

Gate:

- same attached image must be the parent of the output
- stale source ID test fails closed
- kitchen elevation fixture blocks when hood is missing

### Phase 1: Thin-core plugin beside current plugin

Goal: add the new substrate without removing old paths.

Tasks:

- implement `botji-core` tools listed above
- keep `botji-artifacts` available behind legacy skills
- add harnesses for source registration, current-turn lineage, receipt records,
  and delivery gate

Gate:

- source/current-turn lineage harness passes
- receipt gate blocks stale parent, missing parent, and blocked receipt

### Phase 2: Hermes-native photo/sketch path

Goal: make normal `Make 3d` stop using the large artifact pipeline.

Tasks:

- update `botji-render-router` to call `botji-core`
- add `botji-source-current`, `botji-visual-inventory-review`, and
  `botji-delivery-receipt`
- use model vision for the source inventory and output comparison
- use code execution only for deterministic file inspection when needed

Gate:

- one source-bound transform per request by default
- no `artifact_extract_manifest` on normal photos
- no stale artifact IDs across turns
- kitchen elevation regression produces a receipt that blocks missing hood

### Phase 3: Technical files

Goal: move deterministic extraction to skill-led code execution.

Tasks:

- PDF: skill calls code for page count/text/table probes
- DXF: skill calls code for layer/entity counts and bounded bbox only when
  needed
- images: skill calls code for dimensions/EXIF only
- remove generic schema preview as a required render step

Gate:

- large DXF does not traverse bbox by default
- PDF/DXF facts are cited from code output
- render path does not depend on byte-size preview heuristics

### Phase 4: Retire old plugin surfaces

Goal: remove imperative behavior that has moved to skills.

Remove or freeze:

- `_metadata.py` broad adapter engine
- `_normalization.py`
- `_schema_preview.py`
- `_comparators.py`
- `_vision.py`
- semantic review logic in `_review.py`

Keep only compatibility shims until all skills use `botji-core`.

Gate:

- plugin code is smaller than skill code
- production smoke passes on both tenants
- live provider E2E is opt-in and separate

## Rollback

Each phase must be feature-flagged:

- `BOTJI_CORE_ENABLED=0|1`
- `BOTJI_LEGACY_ARTIFACTS_ENABLED=0|1`
- `BOTJI_REQUIRE_CURRENT_TURN_SOURCE=0|1`

Rollback means disabling `BOTJI_CORE_ENABLED` and restoring the previous
container image. Session files should be backed up before any forced rotation.

## Definition of done

- Both tenants share identical skills and plugin surfaces.
- Tenant isolation remains per profile/container/data dir/token/allowlist.
- Plugin code is smaller than skill code.
- A source-bound render cannot deliver with stale source lineage.
- The current kitchen elevation fixture blocks missing extractor hood.
- Normal single-image `Make 3d` uses one transform and one review by default.
- Review receipts explain warnings versus blockers in user-visible language.
