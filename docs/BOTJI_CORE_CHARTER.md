# `botji-core` charter

## What Botji is

Botji is an **audited-workflow harness on Hermes**. Hermes runs the agent loop,
tools, skills, plugins, and orchestration. Botji adds the contract that turns
agent runs into auditable workflows: typed source → evidence → transform →
review → receipt → delivery, with hard gates and durable records at each step.

The render bot (sketch/brief → image) is one workflow Botji runs today. Other
workflows can be added as skills without changing the harness substrate.

## What this charter covers

`botji-core` is the **thin mechanical substrate** of the harness. Its job is
to record durable source/output facts and enforce mechanical delivery rules —
nothing semantic. Everything semantic lives in skills.

V1R PR 11 (2026-05-25) finished draining `botji-artifacts/`: the entire
5,821-LOC plugin was deleted, its render-time spatial-manifest extractor
migrated to `botji-render/manifest.py`, and the legacy `art_*` ID family
retired except as defense-in-depth pre-tool-call guards. If `botji-core` is
allowed to silently grow into a god plugin, the rewrite has failed in a
different shape — keep semantics in skills, not here.

This file is the contract. The CI step in [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
enforces a hard LOC cap on `seed/hermes/plugins/botji-core/`. **Raising the cap
requires editing this file in the same PR.** That friction is intentional — it
forces the "is this mechanical or semantic?" conversation when, and only when,
the budget breaks.

## Allowed in `botji-core`

The substrate may contain logic for the five plan-mandated tools, plus the
small pure utilities they need.

| Allowed | Why it belongs here |
|---|---|
| `source_register` — register a user attachment as a `src_*` artifact | mechanical I/O on already-validated input |
| `source_current` — list `src_*` artifacts for a turn | indexed lookup, no interpretation |
| `artifact_write` — write an `out_*` artifact from a path | mechanical I/O |
| `receipt_record` — persist a `rcpt_*` receipt record | mechanical I/O |
| `delivery_gate` — pass/warn/block on **mechanical** checks on structured data | parent existence, blocked receipt status, missing required fields, safe output paths, current-turn lineage on records that already carry `current_turn_id` |
| `pre_tool_call` hook (`hooks/stale_id_block.py`) — block bad tool calls before dispatch on three orthogonal mechanical predicates: stale `art_*` turn, mixed `art_*`/`src_*`/`out_*`/`rcpt_*` ID family, and over-budget `artifact_transform(edit_image)` per turn | structural checks on already-typed args — no vision, no classification, no prompt construction. The cap and override flags are forwarded from skills, not inferred. |
| Path safety helpers (`_resolve_allowed_path`, secret-path detection) | shared substrate, security-critical |
| Content-addressed hashing, atomic file writes, ID allocation | pure utilities |
| Index I/O (jsonl read/append, atomic replace) | pure utilities |
| Prometheus metrics export (`metrics/`): counter/histogram/info on tool calls + verdicts | mechanical observation — only tool name, operation arg, duration, verdict string. No semantic interpretation. Fail-open. |
| Append-only audit log (`metrics/hooks.py:_audit_*`): JSONL line per substrate tool call with timestamp, tool, tenant, session_id, args_hash, args_kinds, result_status, redacted result_summary, duration_ms | mechanical observation — no raw args, no raw results, secrets regex-redacted before write. Same fail-open contract as metrics. |

## Not allowed in `botji-core`

If you reach for one of these inside `botji-core/`, write a skill instead — or
extend an existing one. The skill layer is the right place to express the
reasoning, the prompt boundaries, and the user-visible failure mode.

- **No source classification.** Deciding whether a file is a sketch, photo,
  manifest, or document is a skill responsibility. The substrate records what
  the caller declared; it does not infer.
- **No vision interpretation.** No image-content inspection, no manifest
  extraction, no element counts. That's `botji-2d-to-3d`, `botji-artifact-fidelity`,
  and friends.
- **No retry policy.** Decisions like "retry with stronger constraints" or
  "fall back to a different route" live in `botji-render-router`,
  `botji-2d-to-3d`, and the prompt contract.
- **No transition-period pipeline rules.** Logic that existed only to bridge
  legacy `art_*` and native `src_*` (e.g. the cross-pipeline source block from
  commit [`1bfcd89`](https://github.com/don-jose-dev/botji-docker-setup/commit/1bfcd89))
  is gone with V1R PR 11. New transition code is not welcome here — keep it in
  skills.
- **No legacy `art_*` acceptance in core tools.** As of [V1R PR 1](BOTJI_V1R.md)
  the tools `source_register`, `output_write`, `receipt_record`, and
  `delivery_gate` treat `art_*` IDs as non-existent — there is no legacy
  fallback at lookup time. The `hooks/stale_id_block.py` pre-tool predicate
  still rejects calls that mix `art_*` and `src_*` IDs in the same arguments
  as cross-pipeline defense — observation is mechanical; acceptance is not.
  V1R PR 11 deleted `botji-artifacts/`, so the legacy index no longer exists;
  the stale-art-record lookup `_check_stale` was retired with it.
- **No user-visible caveat text.** The substrate emits structured verdicts
  (`pass` / `warn` / `block` + machine-readable reasons). Wording is a skill
  concern.
- **No prompt construction.** No template assembly, no brief building, no
  noise-vocabulary checks. Skills own the prompt surface.

## Current cap

<!-- BOTJI_CORE_LOC_CAP: 600 -->

The CI step in `.github/workflows/ci.yml` parses the value from the HTML
comment above. The cap lives here, not in CI, so raising it always requires a
visible change to this file.

## Cap history

| Date | Cap | Why |
|---|---|---|
| 2026-05-22 | 650 → 1000 | Two mechanical additions to the substrate landed together: (1) `hooks/stale_id_block.py` (~131 LOC) — `pre_tool_call` enforces three structural safety rules (stale `art_*`, mixed ID family, over-budget transforms) on already-typed args; (2) `metrics/{__init__,exporter,hooks}.py` (~185 LOC) — Prometheus counters and histograms over tool names, verdict strings, and durations. Both fail open. Both are pure mechanical reads — no vision, no classification, no prompt construction. Cap set to 1000 to accommodate both without artificial pressure to collapse the metrics shape; future bumps must justify against the same "Allowed in `botji-core`" table. |
| 2026-05-22 | 1000 → 1100 | Append-only audit log added as an extension of `metrics/hooks.py` (the existing `post_tool_call` hook). One JSONL line per substrate tool call records timestamp, tool, tenant, session id, args hash + per-field type, success/error status, ≤200-char redacted result summary, and wall-clock duration. No raw args, no raw results, no file contents — secrets matching conservative regex patterns (`tok_*`, `sk_*`, `Bearer *`, JWT `eyJ*`) are stripped before write. Same fail-open contract as the metrics export: any error in the audit path is swallowed and the underlying tool call is unaffected. Mechanical observation, no semantic interpretation. Bump (+100) reserves headroom for the redaction patterns + JSONL serializer + four new audit fixtures' supporting helpers; the actual delta inside `botji-core/` is ~60 LOC. |
| 2026-05-22 | 1100 (unchanged — analysis) | Measurement after the drift-retrospective night: actual `botji-core/` LOC is **1056** against the **1100** cap — 44-LOC headroom (~4 %). Per-file breakdown: `__init__.py` 587, `metrics/hooks.py` 164, `hooks/stale_id_block.py` 125, `hooks/delivery_check.py` 90, `metrics/exporter.py` 74, `__init__.py` stubs 16. No tighten this round: 4 % headroom is already inside the "ratchet only, don't yo-yo" discipline; cutting to 1075 would be cosmetic and would block legitimate small additions without an actual mechanical reduction first. The real cap drop comes from V1R PR 11 (drop `botji-artifacts`), which the [V1R contract](BOTJI_V1R.md) projects at **1100 → ~600**. This row exists to make the analysis visible in the audit trail rather than silent. |
| 2026-05-25 | 1100 → 600 | V1R PR 11 — three structural moves land together to hit the original 1100 → ~600 projection. **(1) Delete `botji-artifacts/`** entirely (28 files, ~5.8k LOC) and migrate its one keeper — spatial-manifest extraction — to `botji-render/manifest.py`. Strip the legacy-defense helpers from core (`_find_legacy_artifact` ~38 LOC, the mixed-pipeline-source guard + `_native_sources_for_turn` in `delivery_gate` ~30 LOC, `_check_stale` ~25 LOC in stale_id_block, stale-lineage path in delivery_check ~15 LOC). **(2) Extract `metrics/`** (Prometheus exporter + JSONL audit log + pre/post_tool_call hooks, ~248 LOC) to a new sibling plugin `botji-observability`. Observability is a *sidecar*, not state — chartering it inside the substrate was the framing error the prior 1100→1000 row encoded; the substrate is state + tools, full stop. **(3) Extract `hooks/`** (`pre_tool_call` mixed-family + transform-budget enforcement + `transform_llm_output` no-receipt rewrite, ~188 LOC) to a new sibling plugin `botji-guards`. "Skills guide; hooks enforce" still holds — the enforcer plugin just isn't the state plugin. The mixed-ID-family predicate stays as cross-pipeline defense (legacy `art_*` IDs can still leak from older receipt history or skill-prose drift); only its location changed. Actual `botji-core/` LOC after the split: **535 / 600** — 65-LOC headroom. Anything that wants to land in core now must pass the "Allowed in `botji-core`" test below; new hooks belong in `botji-guards`, new metrics in `botji-observability`. |

## Raising the LOC cap

1. Edit the `BOTJI_CORE_LOC_CAP` value in the comment above.
2. In the same PR, document either:
   - **Why a new line of logic is mechanical** (link the function and explain
     which row of the "Allowed" table it falls under), or
   - **Why the table needs to grow** (a new mechanical primitive that wasn't
     anticipated).
3. Add a row to the "Cap history" table above with the date, old → new cap,
   and a one-paragraph rationale.

When in doubt: write a skill. Skills are the cheap thing to add, the substrate
is the expensive thing to add.
