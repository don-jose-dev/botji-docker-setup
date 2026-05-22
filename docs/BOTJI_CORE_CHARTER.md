# `botji-core` charter

`botji-core` is the **thin substrate** for the Hermes-native Botji runtime. Its
job is to record durable source/output facts and enforce mechanical delivery
rules — nothing semantic. Everything else lives in skills.

The whole point of [the Hermes-native rewrite](HERMES_NATIVE_REWRITE_PLAN.md) is
to drain ~5,000 LOC of imperative Python out of `botji-artifacts` into
declarative LLM skills. If `botji-core` is allowed to silently grow into a god
plugin, the rewrite has failed in a different shape.

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
- **No transition-period pipeline rules.** Logic that exists only to bridge
  legacy `art_*` and native `src_*` (e.g. the cross-pipeline source block from
  commit [`1bfcd89`](https://github.com/don-jose-dev/botji-docker-setup/commit/1bfcd89))
  belongs in the legacy plugin or in a skill rule. Once Phase 4 retires the
  legacy plugin, transition-period code in core becomes dead weight — and
  reviewers won't notice it leaving because it's small.
- **No user-visible caveat text.** The substrate emits structured verdicts
  (`pass` / `warn` / `block` + machine-readable reasons). Wording is a skill
  concern.
- **No prompt construction.** No template assembly, no brief building, no
  noise-vocabulary checks. Skills own the prompt surface.

## Current cap

<!-- BOTJI_CORE_LOC_CAP: 720 -->

The CI step in `.github/workflows/ci.yml` parses the value from the HTML
comment above. The cap lives here, not in CI, so raising it always requires a
visible change to this file.

### Cap history

- **650 → 720** (PR T2, `feat/hooks-stale-id-and-over-budget`): added the
  `pre_tool_call` hook (`hooks/stale_id_block.py`, ~125 LOC + 6-line
  `hooks/__init__.py`) that enforces three mechanical safety rules at the
  substrate layer instead of at the skill layer (the canonical "skills
  guide; hooks enforce" boundary from the botji-hermes-strategic-position
  memory). The hook reads already-typed args and a single legacy index
  lookup — no vision, no classification, no prompt construction. It is the
  substrate equivalent of `delivery_gate`'s parent-existence check, just at
  pre-dispatch time instead of post-receipt.

## Raising the LOC cap

1. Edit the `BOTJI_CORE_LOC_CAP` value in the comment above.
2. In the same PR, document either:
   - **Why a new line of logic is mechanical** (link the function and explain
     which row of the "Allowed" table it falls under), or
   - **Why the table needs to grow** (a new mechanical primitive that wasn't
     anticipated).

When in doubt: write a skill. Skills are the cheap thing to add, the substrate
is the expensive thing to add.
