# Skill-interaction evals

A bootstrap harness for asserting things about *interactions between Botji
skills* — places where two skills' contracts can compound, conflict, or
silently fail under reduced context (e.g. parallel-render sub-agents).

Two tiers ship today:

1. **Deterministic** (always-on, the only gate). Structural assertions on
   the tool-call trace: tool presence/absence, lineage graph validity,
   ID-family purity, retry budget caps, and noise-vocabulary regressions
   in prompt fields.
2. **LLM judge** (opt-in via `--judge`, **advisory only**). Per-fixture
   `gpt-5.4-mini` call against the fixture's `expected_semantic` block,
   catching semantic regressions structure cannot see — e.g. a
   structurally-valid review whose prose contradicts the source intent.
   Verdicts are printed but **never** change the exit code in this PR.

## Run

    # Deterministic tier only (default)
    python evals/skill_interactions/run_evals.py
    # or
    make eval

    # Deterministic + advisory judge (requires OPENAI_API_KEY)
    python evals/skill_interactions/run_evals.py --judge

    # Write judge verdicts to a JSON report
    python evals/skill_interactions/run_evals.py --judge \
        --judge-report .tmp/judge-report.json

Exit codes: `0` all deterministic pass, `1` any deterministic fail, `2`
fixture load error. **The judge tier never affects the exit code.**

## Layout

    evals/skill_interactions/
      run_evals.py          # runner + check engine + CLI
      judge.py              # Tier 2 LLM-judge module (advisory)
      judge_prompt.md       # system prompt the judge reads at runtime
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

# Optional. When present and --judge is passed, the LLM judge scores
# the trace against these plain-English statements (advisory only).
expected_semantic:
  - One terse statement that should be true about the trace.
  - Another statement, ideally one that structural checks cannot see.
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
5. Optionally add `expected_semantic` — 1-5 plain-English statements that
   should hold true for a correctly-handled trace. Best statements target
   prose/intent the structural tier cannot see (briefs, retry guidance,
   manifest narrative, review reasoning). Synthetic data only — the
   judge sees tool args, so never put real user content in fixtures.
6. Run `python evals/skill_interactions/run_evals.py` to confirm it passes
   (or specifically fails on the violation you intend). To exercise the
   judge tier locally, also pass `--judge` with `OPENAI_API_KEY` set.

## The LLM-judge tier

The judge is a thin wrapper around `gpt-5.4-mini` Responses API with
strict structured output. It is **advisory only** in this PR — the
runner prints verdicts but exit code reflects only the deterministic
tier.

**Schema** (`evals/skill_interactions/judge.py` → `JudgeVerdict`):

| Field | Type | Notes |
|---|---|---|
| `verdict` | `pass` / `warn` / `block` / `inconclusive` | `inconclusive` is the safe default — not a failure |
| `confidence` | 0.0–1.0 | Judge's self-reported confidence |
| `reasoning` | string ≤500 chars | Terse, specific |
| `aligned_constraints` | list[str] | `expected_semantic` statements satisfied |
| `violated_constraints` | list[str] | `expected_semantic` statements contradicted |

**Conservatism**: the prompt instructs the judge to default to
`inconclusive` on any uncertainty. False-positive blocks are worse
than missed catches because the deterministic tier is the actual gate.

**Failure mode**: any error reaching the OpenAI API (auth, rate limit,
network, model unavailable) → judge returns `inconclusive` with the
reason in `reasoning`. The runner never crashes because of the judge.
Without `OPENAI_API_KEY` set, every verdict is `inconclusive` —
that's the expected CI-without-key behavior.

### Cost

`gpt-5.4-mini` with a small structured-output schema is ~$0.01–$0.05
per fixture (we budget the high end). The runner prints the total
estimated cost at end-of-run.

- Default cap: **20 fixtures per `--judge` run** (≤$1.00 worst case).
- Override: `BOTJI_JUDGE_FIXTURE_CAP=N python ... --judge`. Fixtures
  past the cap are skipped (listed in the summary).
- Cost tracking is per-run only — there is no cumulative budget guard.
  When the judge moves to blocking we'll add a monthly cap to the CI
  job. See [`docs/OBSERVABILITY.md`](../../docs/OBSERVABILITY.md) for
  observability notes.

### Privacy

The judge sees the **full trace** including all tool args
(`mood_brief`, `instructions`, paths, IDs, retry guidance, manifest
contents). **Use synthetic data only in fixtures.** Never include
real user content, real customer paths, real Hermes session content,
or anything you would not paste into the OpenAI dashboard.

The judge does not see anything outside the fixture+trace payload — no
seed config, no env vars, no source code.

### Promoting the judge to blocking (future PR)

Today's contract is "judge is advisory; deterministic tier is the
gate." Promotion criteria for a future PR:

1. **Stability ≥95% on baseline.** Run `--judge` across all fixtures
   on a known-good baseline at least 20× and confirm verdicts agree
   ≥95% of the time. Lower stability means the judge is too noisy to
   be a gate.
2. **Confidence calibration.** Median confidence on `pass` verdicts
   should be ≥0.8; on `block` ≥0.7. If the judge keeps returning
   high-confidence-wrong, do not promote.
3. **Cost budget.** A monthly OpenAI spend cap is in place for the CI
   job (a `BOTJI_JUDGE_MONTHLY_CAP_USD` env var or similar).
4. **Override mechanism.** When promoted, an `eval-judge-override`
   PR label (or `JUDGE_OVERRIDE=1` env var) MUST be available so a
   maintainer can land a PR over a judge `block` if the verdict is
   wrong. The override creates an audit-log entry; the deterministic
   tier remains uncompromised.

The promotion PR also flips the `--judge` default to on in CI and
changes the runner exit code to reflect judge `block` verdicts. The
deterministic tier's role does not change.

## CI

Two GitHub Actions jobs are registered in `.github/workflows/ci.yml`:

- **`skill-eval`** — deterministic tier. `workflow_dispatch` only
  today; flips to auto-on-PR when real recorded traces replace
  `mock_trace`.
- **`skill-eval-judge`** — deterministic + judge tier.
  `workflow_dispatch` **only**, never auto-run because of cost. Uses
  the `OPENAI_API_KEY` repo secret. Without the secret set, the job
  still runs cleanly — every verdict is `inconclusive` (the safe
  fallback). Document the requirement in the workflow's docstring.

## Follow-up

- Wire to real agent runs so `mock_trace` becomes `trace`.
- Capture `baseline.json` and gate Phase 2 PRs on no-regression
  (deterministic + judge stability check).
- Move the judge to blocking once the promotion criteria above are
  met.
