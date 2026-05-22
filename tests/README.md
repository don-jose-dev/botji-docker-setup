# Botji deterministic harness fixtures

Fixtures consumed by `runtime/bin/botji-harness`. One subdirectory per suite;
the runner picks up every `*.yaml` file inside.

| Suite | Replaces | Plugin under test |
|---|---|---|
| `core` | `botji-core-harness` | `botji-core` |
| `gate` | `botji-gate-harness` | `botji-gate` |
| `allowlist` | `botji-allowlist-harness` | `botji-allowlist` |
| `contract` | `botji-contract-new` | (Prompt v1 schema) |
| `fidelity-guard` | `botji-fidelity-guard-harness` | `botji-artifacts` (`_guardrails`, `_vision`) |
| `id-family` | (new) | `botji-core` — mixed `art_*`/`src_*`/`out_*` rejection |

## Fixture schema

```yaml
id: stable-identifier            # filename stem if omitted
description: human readable summary
inputs:
  ...                            # suite-specific setup
expected:
  ...                            # suite-specific predicates
```

See `runtime/bin/botji-harness` docstrings for per-suite `inputs` / `expected`
keys.

## Running

```
# one suite
botji-harness run --suite core

# all suites (CI default)
botji-harness run --suite all
```

The runner discovers fixtures from `tests/fixtures/<suite>/`. Override with
`BOTJI_HARNESS_FIXTURES=/path/to/fixtures`. Inside the container the runner
prefers `$HERMES_HOME/tests/fixtures` (if mounted), else falls back to the
repo seed.
