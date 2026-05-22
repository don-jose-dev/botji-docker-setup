# cron/

Declarative seeds for Hermes cron jobs. Each `*.yaml` here is a spec
that the deploy pipeline copies into every tenant's data dir, where
the on-VPS registration step turns it into a record in
`~/.hermes/cron/jobs.json`.

## Layout

```
cron/
  nightly_regression.yaml         # cron job spec
  scripts/
    botji-nightly-regression.sh   # the actual command the job runs
```

`scripts/deploy-tenants.sh::sync_code_components()` copies:

- `cron/*.yaml` → `$DATA_DIR/cron/`
- `cron/scripts/*` → `$DATA_DIR/scripts/` (Hermes resolves cron `script:`
  paths against `$HERMES_HOME/scripts/`; under our compose mount
  `HERMES_HOME` == `$DATA_DIR`).

Files matching `*.disabled` are skipped — see "Disabling a job" below.

## Field reference

The yaml fields mirror the kwargs of upstream
[`hermes-agent/cron/jobs.py::create_job`](https://github.com/NousResearch/hermes-agent/blob/main/cron/jobs.py).
Full semantics: `hermes-agent/website/docs/user-guide/features/cron.md`
("No-agent mode" section).

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Human label; also accepted by `hermes cron <action> <name>`. |
| `schedule` | yes | Cron expression, ISO timestamp, or natural-language interval. |
| `no_agent` | when scripted | `true` runs the script directly — no LLM, no tokens. |
| `script` | when `no_agent=true` | Relative path under `$HERMES_HOME/scripts/`. `.sh` → bash, anything else → Python. |
| `prompt` | always | Empty string for no_agent jobs; the prompt for agent-backed jobs. |
| `deliver` | no | `local` (default for cron), `origin`, `telegram`, etc. |

Skills, workdir, profile, context_from, enabled_toolsets, etc. are also
accepted — yaml field names are identical to the Python kwargs.

## Disabling a job

Rename the yaml to add a `.disabled` suffix:

```sh
mv cron/nightly_regression.yaml cron/nightly_regression.yaml.disabled
```

The next deploy will skip seeding it. The yaml seed and the live
`~/.hermes/cron/jobs.json` are decoupled — if the job is already
registered, also pause it on the VPS:

```sh
docker exec botji-hermes hermes cron pause botji-nightly-regression
```

See `docs/OPERATIONS.md` "Nightly regression cron" for the full
operational runbook, including how to add a new suite to the nightly
run and where to read the output.
