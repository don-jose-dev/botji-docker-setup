# Botji V1R — implementation rewrite of V1

## What this is

`V1R` is the **implementation rewrite** of Botji V1. The product contract from [`BOTJI_V1.md`](BOTJI_V1.md) is unchanged: source → evidence → transform → review → receipt → delivery. The implementation gets cleaned: native IDs only, declarative review axes, adapter protocol, no `botji-artifacts` plugin.

V1R is **not V2**. V2 should mean a real product change (typed manifests everywhere, new workflow types, etc.). V1R is plumbing.

Botji is an **audited-workflow harness on Hermes**. Render is one workflow it runs today. V1R cleans the harness substrate so additional workflows can be added without paying tax for the legacy implementation shape.

## Why a rewrite

The current V1 implementation carries four pieces of debt:

- Legacy `art_*` ID family alongside native `src_*`/`out_*`/`rcpt_*`/`ev_*` — the source of the [stale-lineage SHA mask](../../../../Users/Don%20Jose/.claude/projects/C--Dev-botji-docker-setup/memory/stale_art_lineage_sha_mask.md) class of bug. Even after the turn-salt fix, the dual pipeline remains a structural risk.
- ~5,000 LOC of imperative Python in `botji-artifacts` that the [Hermes-native rewrite plan](HERMES_NATIVE_REWRITE_PLAN.md) was supposed to drain into declarative skills. The drain hasn't happened.
- Procedural 8-axis review branches in `_review.py` that should be declarative data.
- Per-file-type extract/normalize/compare logic scattered across the plugin instead of behind a typed adapter protocol.

V1R fixes all four in one disciplined sequence.

## Target shape

```
seed/hermes/plugins/
  botji-core/                         # mechanical substrate (charter-bound, ≤600 LOC after V1R)
    ids.py                              # src_/out_/ev_/rcpt_ only — no art_*
    store.py                            # jsonl/sqlite registry, atomic writes
    files.py                            # path safety, hashing, copy/store
    tools.py                            # source_register / output_write / evidence_record / receipt_record / delivery_gate
    gate.py                             # mechanical checks only
    hooks.py                            # pre_tool_call (stale source / mixed family / over-budget)
  botji-adapters/                     # per-file-type extract/normalize/compare
    image.py
    pdf.py
    text.py
    dxf.py
    docx.py
    xlsx.py
    registry.py                         # Adapter protocol registration
  botji-review/                       # axis evaluation
    axes.yaml                           # declarative axis definitions (not procedural branches)
    engine.py                           # loads axes, produces verdict from evidence
    vision.py                           # optional model-based review provider
    comparators.py                      # deterministic comparator registry
  botji-render/                       # generation operations + providers
    providers/openai_codex.py
    operations.py                       # exact_copy, render_schema, edit_image

seed/hermes/skills/
  botji-render-router/                  # workflow routing (native IDs only)
  botji-source-current/                 # current-turn source lookup
  botji-delivery-receipt/               # receipt finalization
  botji-premium-brief/                  # render vocabulary contract
  botji-artifact-fidelity/              # calls botji-review engine; no procedural logic
  botji-2d-to-3d/
  botji-cad-elevation/
  botji-photo-mode/
```

The rule: **core stores facts; adapters extract facts; review evaluates facts; skills decide workflows.**

`botji-artifacts/` is **deleted** by PR 11. Nothing survives the V1R sequence under that name.

## Migration rules

1. **Hard cutover.** No mixed-state living rooms. Each PR leaves the codebase in one consistent shape for its surface — either fully old or fully new. The Telegram bots may go down for ~5 minutes during cutover deploys; that is the budget.
2. **No bridge code** — with one explicit exception: PR 10 introduces `artifact_*` → `source_*` aliases for one release cycle. Every alias carries a `# DELETED_BY: PR_11` comment. CI enforces the marker. PR 11 removes them.
3. **Ship-with-consumer.** A PR merges only if the code it adds has a runtime caller in-tree at merge time. Adapter PRs (2–7) switch consumers and delete the legacy path in the same PR.
4. **Net LOC delta is non-positive** for PRs 2–11. Each adapter PR and the review-axes PR must delete at least as much as it adds.
5. **VPS smoke between every merge.** `scripts/vps-postdeploy-smoke.sh` against `/opt/botji`. Both tenants must come up green. Failed smoke = stop, no auto-rollback.
6. **One PR per session.** No parallel V1R PRs. Tonight proved why.

## The 12 PRs

### PR 0 — V1R contract doc (this file)
**Done:** `docs/BOTJI_V1R.md` exists in master.
**Kill:** N/A — doc-only.

### PR 1 — Lock native IDs in botji-core
Remove any path in `botji-core` that treats `art_*` as legitimate input. Charter update: explicit "no `art_*` accepted in core tools." Existing mixed-family `pre_tool_call` hook stays as defense-in-depth.

**Done:** Grep `art_*` in `botji-core/` returns only comments referencing the migration history.
**Kill:** If `art_*` removal breaks any test, the test is updated to use native IDs in same PR — no test deletions.

### PR 2 — Adapter protocol + image adapter (paired)
New `botji-adapters/` plugin with `Adapter` protocol (`detect / extract / normalize / compare`). Implement `image.py`. Same PR switches all image-extract / image-compare paths in `botji-artifacts` to the new adapter and deletes the now-orphan image code.

**Done:** Image workflow runs end-to-end calling `botji-adapters/image`. Grep image-specific functions in `botji-artifacts/` returns nothing.
**Kill:** Adapter protocol fails review (god-class shape, wrong abstraction level) — redesign before proceeding.

### PRs 3–7 — Adapter migration, one file type per PR
PR 3 pdf, PR 4 text, PR 5 dxf, PR 6 docx, PR 7 xlsx. Each: adds adapter, switches consumers, deletes equivalent code from `botji-artifacts`.

**Done:** That file type's logic exists only in `botji-adapters/<type>.py`. Zero references in `botji-artifacts`.
**Kill:** Net LOC delta positive — refactor before proceeding.

### PR 8 — Declarative review axes
Move 8-axis review from `_review.py` procedural branches into `botji-review/axes.yaml` + small engine loader. Switch `botji-artifact-fidelity` skill to call the engine. Delete procedural code in same PR.

**Done:** `_review.py` is gone or stub-only. Axis definitions live in YAML.
**Kill:** Engine grows beyond ~100 LOC — that's a god-class signal; refactor.

### PR 9 — Render plugin consolidation
New `botji-render/` plugin with `providers/openai_codex.py` and `operations.py` (`exact_copy`, `render_schema`, `edit_image` as named functions, not switch statements). Move provider logic out of `botji-artifacts`. Consumer: `botji-render-router`.

**Done:** Render workflow calls only `botji-render/`. Legacy `artifact_transform` paths deleted.
**Kill:** Operation function exceeds ~50 LOC — split or push detail into provider.

### PR 10 — Tool surface rename + kill-marked aliases
Rename: `artifact_register` → `source_register`, `artifact_extract` → `evidence_extract`, `artifact_transform` → `operation_run`, `artifact_review` → `review_record`. Old names become aliases for one release cycle with `# DELETED_BY: PR_11` comment. CI enforces the marker.

**Done:** All skills call new names. Aliases exist but marked. CI passes the deletion-marker check.
**Kill:** Any skill still calls an old name without updating — fix in same PR.

### PR 11 — Drop aliases, drop botji-artifacts
Remove the aliases from PR 10. Delete `seed/hermes/plugins/botji-artifacts/` entirely. Verify zero `art_*` references outside historical receipt records (those stay as audit history).

**Done:** `botji-artifacts/` does not exist. Grep `art_` in `seed/hermes/plugins/` returns only historical-receipt references.
**Kill:** Any active code path references `art_*` — fix before merge.

After PR 11 merges, the charter LOC cap drops from 1000 → ~600.

### PR 12 (BLOCKED) — Source-bound image edit
Requires upstream Hermes extending `ImageGenProvider` with source-bound semantics (`source_id` + `preserve_regions` in the provider interface). Either wait for upstream, or open Hermes PR first (separate repo, separate cadence). NOT on the V1R critical path.

## Operational rules during V1R

- **VPS smoke after every merge.** Failed smoke → stop. Both tenants must come up green.
- **No parallel V1R PRs.** PRs 3–7 might look parallel-friendly but each depends on the adapter protocol stabilizing in PR 2. One at a time.
- **LOC ledger.** Each PR description includes `+X / -Y / net Z` for `seed/hermes/plugins/`. Net Z must be ≤ 0 for PRs 2–11.
- **Charter compliance.** If a PR adds logic to `botji-core/`, the PR description must justify it against the "Allowed" table in [`BOTJI_CORE_CHARTER.md`](BOTJI_CORE_CHARTER.md). No silent expansion.
- **No new paid services.** Codex subscription only. No new API keys, no new vendors. Open-source-in-compose for anything else.

## Cap ledger

| After PR | Cap | Why |
|---|---|---|
| current | 1000 | Pre-V1R baseline |
| PR 1 | 1000 | Lock native; small reduction but cap holds |
| PR 11 | ~600 | botji-artifacts gone; cap drops to match real mechanical surface |

## Post-V1R tracks (separate plans, start after PR 11)

- **Typed manifest end-to-end** (Phase C from the Hermes-native plan). Schema → extract → review → receipt as typed entities throughout.
- **Local LLM in compose for the eval judge.** Ollama + small model in `docker-compose.yml`; no API key. Wires up the deterministic judge harness at `evals/skill_interactions/judge.py` with a real model.
- **Real-traffic observability.** Grafana + Alertmanager added to compose. No vendor.
- **Hermes upstream PR.** Source-bound `image_edit` semantics added to `ImageGenProvider`. Unblocks PR 12.
- **DR backup.** restic to a second mounted volume on the VPS, or a peer-VPS rsync. No S3, no vendor.

## How this doc is enforced

- Every V1R PR title starts with `V1R PR N — <subject>` and references this file.
- PR description includes the LOC delta line.
- Charter edits are gated on adding a cap-history row in `BOTJI_CORE_CHARTER.md`.
- "Done" and "Kill" criteria in this doc are the merge gate. If a PR doesn't meet "Done" or trips "Kill," it doesn't merge — no exceptions.
- Drift signal: if more than one V1R PR is open at the same time, stop and reconsider before merging.
