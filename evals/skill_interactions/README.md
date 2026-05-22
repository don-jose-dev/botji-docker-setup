# Skill-interaction evals

A bootstrap harness for asserting things about *interactions between Botji
skills* — places where two skills' contracts can compound, conflict, or
silently fail under reduced context (e.g. parallel-render sub-agents).

This is the deterministic tier. It checks structural properties of a
recorded tool-call trace: tool presence/absence, lineage graph validity,
ID-family purity, retry budget caps, and noise-vocabulary regressions in
prompt fields. The LLM-judge tier (semantic verdicts against
[`source_fidelity_review.schema.json`](../../seed/hermes/schemas/source_fidelity_review.schema.json))
is a follow-up.

## Run

    python evals/skill_interactions/run_evals.py
    # or
    make eval

Exit codes: `0` all pass, `1` any fail, `2` fixture load error.

## Layout

    evals/skill_interactions/
      run_evals.py          # runner + check engine (single file, ~180 LOC)
      fixtures/             # one YAML per scenario
      README.md             # this file

## What a fixture looks like

```yaml
id: <short_kebab_case>
risk_pairs: [skill-a, skill-b]      # which skill contracts are in tension
description: |
  Why this scenario exists.

mock_trace:                          # synthetic for now; recorded later
  - tool: artifact_register
    args: { ... }
    result: { artifact_id: ... }

deterministic_checks:
  - kind: tool_call_present
    tool: artifact_register
    min_count: 2
  - kind: lineage_match
  - kind: transform_count_max
    per_branch: 2
```

## Available check kinds

| Kind | Purpose |
|---|---|
| `tool_call_present` | At least `min_count` calls to `tool` exist |
| `tool_call_absent` | Zero calls to `tool` |
| `no_id_family_mix` | No single call mixes `art_*` with `src_*`/`out_*`/`rcpt_*` |
| `lineage_match` | Every output's `parents` ⊆ registered source IDs |
| `transform_count_max` | `artifact_transform` count ≤ `per_branch` |
| `no_banned_vocabulary` | No noise vocab in `instructions`/`mood_brief`/etc |

## Adding a fixture

1. Copy an existing YAML in `fixtures/` to a descriptive new name.
2. Edit `id`, `risk_pairs`, `description`.
3. Replace `mock_trace` with the tool-call sequence you want to assert
   against. Each step is `{ tool, args, result }`.
4. Pick the smallest set of `deterministic_checks` that would catch a real
   regression in the risk pair.
5. Run `python evals/skill_interactions/run_evals.py` to confirm it passes
   (or specifically fails on the violation you intend).

## CI

A `workflow_dispatch`-only job is registered in `.github/workflows/ci.yml`.
The harness is not yet on the auto-PR critical path — that arrives with
the LLM-judge tier when real recorded traces are available.

## Follow-up

- LLM judge tier (gpt-5.4-mini, structured output against the existing
  fidelity-review schema, ~$0.01/fixture).
- Wire to real agent runs so `mock_trace` becomes `trace`.
- Capture `baseline.json` and gate Phase 2 PRs on no-regression.
