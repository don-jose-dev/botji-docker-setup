# Operations runbook

This document is the day-2 reference for running the botji-hermes deployment.
For first-time setup see [DEPLOYMENT.md](DEPLOYMENT.md); for the product overview
see [BOTJI_V1.md](BOTJI_V1.md); for the Prometheus metrics + Grafana dashboard
see [OBSERVABILITY.md](OBSERVABILITY.md).

---

## Deploy pipeline

```
push to master ──> GitHub Actions
                   ├── build  (Dockerfile → ghcr.io/<owner>/botji-hermes:sha-…)
                   └── deploy (scp deploy artifacts → ssh → vps-deploy.sh)
                               ├── git pull master
                               ├── write .env from CI secret
                               ├── docker pull <image>
                               ├── copy seed/ → /opt/botji/data
                               ├── pre-flight smoke (smoke-plugin.sh) ◀── gate
                               ├── preserve/seed Codex auth, then write Hermes provider auth
                               ├── clear Telegram webhook
                               └── docker compose up -d --force-recreate
```

The pre-flight smoke imports every botji-artifacts submodule and verifies the
public symbols exist *before* the container restarts. A broken import aborts the
deploy with exit 2 — the existing container keeps running.

CI also runs the same smoke on every PR (`.github/workflows/ci.yml`) so a broken
plugin can't reach master in the first place.

### Manual deploy from a workstation

```sh
ssh -i ~/.ssh/botji_deploy root@<vps>
cd /opt/botji
git pull origin master
PLUGIN_DIR=$(pwd)/data/botji/plugins/botji-artifacts \
  bash scripts/smoke-plugin.sh             # explicit smoke before restart
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate
```

### Rollback

```sh
# To a previous image (last 5 sha-… tags are kept on GHCR):
ssh root@<vps>
cd /opt/botji
BOTJI_PROD_IMAGE=ghcr.io/<owner>/botji-hermes:sha-<short> \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

To roll back code-only changes (config, plugin) without changing image:

```sh
cd /opt/botji
git log --oneline -5
git reset --hard <known-good-sha>
cp -R seed/hermes/plugins/botji-artifacts/. data/botji/plugins/botji-artifacts/
docker compose restart hermes
```

---

## Smoke tests

| When | Command | What it does |
|---|---|---|
| Pre-deploy (auto) | `bash scripts/smoke-plugin.sh` from `vps-deploy.sh` | Imports every plugin submodule, verifies public symbols |
| CI (auto) | `.github/workflows/ci.yml` | Same smoke + shellcheck on deploy scripts |
| Post-deploy (manual) | `bash scripts/smoke-agent.sh` | Exercises an end-to-end agent turn against the live API |
| VPS diagnostic | `bash scripts/vps-diag.sh` | Logs, artifact index, recent reviews, config, auth status |

The smoke can also run inside the live container:

```sh
docker cp scripts/smoke-plugin.sh botji-hermes:/tmp/smoke.sh
docker exec botji-hermes bash /tmp/smoke.sh
```

---

## Nightly regression cron

A Hermes cron job runs the full deterministic harness suite every
night at **03:30 local on the VPS** via the upstream `no_agent=True`
primitive — no LLM, no tokens. It shells out to `botji-harness run
--suite all` and only delivers output on failure.

| Property | Value |
|---|---|
| Job name | `botji-nightly-regression` |
| Schedule | `30 3 * * *` |
| Mode | `no_agent: true` |
| Spec / script | `cron/nightly_regression.yaml`, `cron/scripts/botji-nightly-regression.sh` (both seeded per-tenant by `sync_code_components`) |
| Persistent log | `/opt/data/logs/botji-harness/nightly-<UTC>.log` (one file per run, full stdout+stderr) |
| Scheduler output | `~/.hermes/cron/output/<job_id>/<UTC>.md` (only on failure; happy path emits empty stdout = silent tick) |
| Tenants | Both `/opt/botji` and `/opt/botji-degain` run their own copy under their own `HERMES_HOME` |

`--suite all` picks up every suite registered in
`runtime/bin/botji-harness::SUITE_ORDER`, so new deterministic checks
ship by adding a fixture / suite — no edit to the cron job needed. To
add a suite: add `run_<suite>` to the harness, register it in `SUITES`
and `SUITE_ORDER`, and drop fixtures under `tests/fixtures/<suite>/`.

### Reading the output

Happy path: silent. Triage on failure starts at the persistent log:

```sh
ssh -i ~/.ssh/botji_deploy root@<vps>
ls -lt /opt/data/logs/botji-harness/ | head
tail -100 /opt/data/logs/botji-harness/nightly-<UTC>.log
```

### Disabling the cron

Rename the yaml seed to add `.disabled`:

```sh
git mv cron/nightly_regression.yaml cron/nightly_regression.yaml.disabled
```

The next deploy skips seeding it. The yaml seed and the live
`~/.hermes/cron/jobs.json` are decoupled — if the job is already
registered, also pause it on the VPS:

```sh
docker exec botji-hermes hermes cron pause botji-nightly-regression
```

Field mapping for `cron/*.yaml`: see `cron/README.md`.

---

## Kanban-backed render retry

Source-bound renders (sketch / floor plan / photo → 3D) carry a manifest and
must survive a blocked review with at most one retry. The orchestration shape
— manifest extract → transform → review → retry → review → deliver | escalate
— is encoded as a Hermes Kanban workflow template at
`seed/hermes/kanban/workflows/render_retry.yaml`, seeded into
`$DATA_DIR/kanban/workflows/` on every deploy. Each step writes
`current_step_key` on the kanban task row, so a chain that was mid-retry when
the container restarted resumes from where it stopped instead of being lost.

The matching skill prose lives in `botji-2d-to-3d` SKILL.md Step 5 and
references the template by name. The skill stays the source of truth for *what
the retry should do* (manifest-driven brief, adjacency-first
`subject_inventory`, `prior_blocker` verbatim) — the template stays the source
of truth for *which steps run in what order and how to resume*.

### Inspecting a stuck retry chain

A retry chain that hasn't reached `step_deliver` or `step_ask_user` shows up
as a kanban task with `workflow_template_id = "render_retry_v1"` and a
non-null `current_step_key`.

```sh
# All in-flight render-retry tasks
docker exec botji-hermes hermes kanban list \
  --workflow-template-id render_retry_v1 \
  --status running

# Detailed view of one task, including its run history
docker exec botji-hermes hermes kanban show <task-id>

# Or filter to a specific step
docker exec botji-hermes hermes kanban list \
  --workflow-template-id render_retry_v1 \
  --current-step-key step_retry_transform
```

`hermes kanban show` prints the task header, the current `step_key`, and the
list of run rows (each transform / review attempt — see `task_runs` in the
v1 spec). Step transitions and review verdicts appear as `task_events`.

### Manually advancing or cancelling a chain

A chain that's truly stuck (network blip, Codex hung mid-run, etc.) can be
nudged or cancelled:

```sh
# Push a stuck task back to 'ready' so the dispatcher reclaims it on the next tick
docker exec botji-hermes hermes kanban status <task-id> ready

# Force-block with a structured reason — gateway notifier will relay to the user
docker exec botji-hermes hermes kanban block <task-id> \
  --reason "manually cancelled by operator: <why>"

# Archive a completed-but-noisy chain
docker exec botji-hermes hermes kanban archive <task-id>
```

If multiple chains piled up because the dispatcher was down, `hermes kanban
list --workflow-template-id render_retry_v1 --status ready` shows the queue
depth.

### Failure mode: Kanban unreachable → inline fallback

If `kanban.dispatch_in_gateway` is `false` in `config.yaml`, or the kanban
SQLite DB is unavailable, the skill-following agent falls back to running the
same `1 + 1` retry inline — see the prose in `botji-2d-to-3d` SKILL.md Step 5.
Semantics are identical (same retry call, same prompt on double-block); the
only thing lost is durability across container restarts.

This fallback is intentional: it means the deploy can ship the template ahead
of the v2 dispatcher routing without any tenant outage if Kanban is
misconfigured. To verify the active mode for a tenant:

```sh
docker exec botji-hermes hermes config get kanban.dispatch_in_gateway
docker exec botji-hermes ls -la /opt/data/kanban/workflows/
```

If both are present and on, the agent prefers the template. If either is
missing, the agent silently runs inline — no error, no degradation in user
experience.

---

## Log locations

| Stream | Path | Notes |
|---|---|---|
| Container stdout | `docker logs botji-hermes` | Boot output, signal handling |
| Gateway events | `/opt/data/logs/gateway.log` | Inbound/outbound, session lifecycle |
| Agent turns | `/opt/data/logs/agent.log` | Per-turn tool calls + Codex API timings |
| Errors | `/opt/data/logs/errors.log` | WARNING and above only |
| Caddy access | `/var/log/caddy/access.log` (host) | Dashboard reverse proxy hits |
| Nightly regression | `/opt/data/logs/botji-harness/nightly-<UTC>.log` | One file per cron tick (see "Nightly regression cron") |

Docker logs are bounded: `json-file` driver, 5 × 50 MB files (set in
`docker-compose.prod.yml`). Older logs rotate out automatically.

The healthcheck `curl /health` runs every 60s — these access-log lines show up
in `agent.log`. To grep around them: `grep -v 'GET /health' /opt/data/logs/agent.log`.

---

## Fidelity pipeline overview

User sends image + transform instruction (e.g. "make 3d") → gateway routes to
`botji-artifacts` plugin:

```
artifact_register   ─── source image becomes a tracked artifact
        │
artifact_extract    ─── vision_analyze enriches the source
        │
artifact_normalize  ─── builds a hard-requirements schema
        │
artifact_transform  ─── operation = edit_image | render_schema | exact_copy
        │
artifact_review     ─── verdict = pass | warn | block + delivery_gate
```

The review response always carries top-level fields the agent must inspect:

- `delivery_gate` — `clear` / `warned` / `blocked`
- `recommended_action` — `deliver` / `deliver_with_warning` / `do_not_deliver__retry_with_corrections`
- `primary_blocker` — first hard conflict (when blocked)
- `retry_guidance` — first correction (when blocked)

When `delivery_gate == "blocked"`, the agent must not ship the artifact; it must
retry with the corrections list or surface the blocker text to the user.

### Why each axis exists

Per-axis severities are independent — no cascade. See `_review.py:_build_review`
for the full mapping.

| Axis | Owns | Blocks on |
|---|---|---|
| `source_coverage` | source list | empty |
| `preserve_change` | content fidelity | hard vision conflicts |
| `groundedness` | lineage + route | invalid lineage/route only |
| `uncertainty` | review completeness | vision failure / warning |
| `actionability` | structural usability | lineage/route invalid |
| `artifact_lineage` | parent IDs | missing lineage |
| `adapter_route` | route shape | non-`artifact_transform.*` route |
| `modality_comparator` | file metadata | non-image schema drift only |
| `high_fidelity_provider_transform` | Codex provider proof | wrong model/quality |
| `transform_contract_fidelity` | contract sum | structural OR hard vision conflicts |
| `content_fidelity` | visual content | hard vision conflicts only |
| `layout_fidelity` | spatial layout | vision + geometry |
| `geometry_fidelity` | geometric correctness | (image) vision only; (other) vision + modality |

---

## Image routing (gateway → chat model)

Hermes decides per-turn whether user-attached images go to the chat model as
**native pixels** or as a **text description** produced by `vision_analyze`.
The decision is made in `agent/image_routing.py::decide_image_input_mode()`
from two inputs:

1. `agent.image_input_mode` in `config.yaml` — `native` / `text` / `auto`.
2. The active provider/model's `supports_vision` flag in `models.dev`.

### Current setting

```yaml
agent:
  image_input_mode: native    # pixels go straight to the chat model
```

Native is the right choice here because `openai-codex/gpt-5.4-mini` is
multimodal (`supports_vision=True`) and the auxiliary vision backend and the
chat model share the same OAuth — pre-analyzing buys nothing and costs ~7 s
per image turn plus describe-drift fidelity loss.

### When to revert to `auto` or `text`

Set this to `auto` if you switch the chat model to one that is *not*
vision-capable (auto falls back to text mode gracefully when capability
lookup returns False). Set to `text` only if prompt-cache savings on
image-carrying turns outweigh the fidelity cost — typically when the same
image is iterated on across many turns and you can afford the describe step.

### Verifying the routing

After deploy, the next image turn should log either:

```
Image routing: native (mode=native).
```

or the legacy:

```
Image routing: text (mode=text). Pre-analyzing 1 image(s) via vision_analyze.
```

If you still see `mode=text` with `image_input_mode: native` in config, the
container hasn't picked up the new `config.yaml` — `docker compose restart hermes`.

### Trade-off summary

| | text mode | native mode (current) |
|---|---|---|
| Per-image-turn overhead | +7 s for `vision_analyze` | 0 s |
| Image fidelity | text-described (lossy) | pixels (exact) |
| Cache on image turns | cached (text stub) | miss |
| Cache on text follow-ups | cached | cached |
| Failure if model loses vision | graceful text fallback | hard 4xx |

---

## Telegram operations

### Add a user (preferred — file-backed allowlist, no restart)

Edit `/opt/botji/data/botji/allowed_users.json`:

```json
{
  "users": ["825981247", "6912656288", "<new-id>"],
  "_comment": "Each entry is a Telegram numeric ID. Hot-reloaded on next message; no restart needed."
}
```

The `botji-allowlist` plugin watches the file via mtime and re-reads on
every Telegram message. The new user can DM immediately — no restart, no
container recreate, no env-var edit.

To find a user's Telegram ID: have them DM the bot once, then
`grep 'rejected telegram user' /opt/data/logs/gateway.log` — the ID is logged.

### Add a user (legacy — env-var fallback)

If `allowed_users.json` is missing on first boot, the plugin bootstraps it
from the `TELEGRAM_ALLOWED_USERS` env var. After bootstrap, the env var is
ignored. To revert to env-var mode, delete the JSON file and recreate the
container — but the file-backed path is strictly better and you should
prefer it.

### Get a user's Telegram ID

Have them DM the bot once. The rejection log contains the ID:

```
grep 'Unauthorized user' /opt/data/logs/errors.log
```

---

## Common runtime issues

### Plugin load failure on startup

Symptom in `/opt/data/logs/errors.log`:

```
hermes_cli.plugins: Failed to load plugin 'botji-artifacts': cannot import name 'X' from 'Y'
```

Diagnosis:

```sh
docker cp scripts/smoke-plugin.sh botji-hermes:/tmp/smoke.sh
docker exec botji-hermes bash /tmp/smoke.sh
```

Resolution: fix the missing import in the relevant module, rerun
`vps-deploy.sh` — the pre-flight smoke catches this before container start.

### Gateway SIGTERM cycle without container restart

Symptom in `gateway.log`:

```
Received SIGTERM — initiating shutdown
Exiting with code 1 (signal-initiated shutdown without restart request)
Previous gateway exited cleanly — skipping session suspension
```

This is normal during deploys — `docker compose restart` sends SIGTERM through
tini to the gateway process and hermes' internal supervisor restarts it.
RestartCount stays 0 because the *container* never died.

If you see this cycle outside of deploy windows, check:

1. Recent `docker compose` operations in shell history.
2. CI workflow runs in GitHub Actions (deploy job restarts the container).
3. Cron jobs touching `/opt/botji` files (config file-watch triggers reload).

### Codex auth expired

Symptom in `errors.log`:

```
Primary provider auth failed: No Codex credentials stored. Run `hermes auth`...
```

Do not copy one `auth.json` across bots. Codex OAuth refresh tokens rotate and
are single-use; replaying an older GitHub secret or sharing one file between
tenants causes `refresh_token_reused`.

Resolution is per tenant:

```sh
# Fresh login for primary botji tenant.
CODEX_HOME="$PWD/.codex-botji-auth" codex login
base64 -w 0 "$PWD/.codex-botji-auth/auth.json" | gh secret set CODEX_AUTH_B64 --env production

# Fresh, separate login for degain tenant. Same ChatGPT account is OK; same
# auth.json file is not.
CODEX_HOME="$PWD/.codex-degain-auth" codex login
base64 -w 0 "$PWD/.codex-degain-auth/auth.json" | gh secret set CODEX_AUTH_B64_DEGAIN --env production

gh workflow run "Build and Deploy" --ref master
```

The deploy script preserves a newer VPS-local OAuth chain and only imports the
staged secret when it is newer or when `BOTJI_FORCE_CODEX_AUTH_SYNC=1` is set.
This keeps routine deploys from rolling production back to a consumed refresh
token while still allowing an intentional credential rotation.

---

## Health endpoints

| Endpoint | What it reports |
|---|---|
| `http://127.0.0.1:8642/health` | Gateway aliveness (used by docker healthcheck) |
| `http://127.0.0.1:9119/` | Dashboard (insecure; protect via Caddy basic-auth in `caddy/Caddyfile`) |
| `docker inspect botji-hermes --format '{{.State.Health.Status}}'` | Container health summary |

---

## Capacity guidelines

Defaults in `docker-compose.yml` / `docker-compose.prod.yml`:

- Memory limit: 3 GB (prod), 4 GB (dev)
- CPU limit: 1.5 cores (prod), 2.0 cores (dev)
- Artifact max size: 100 MB per artifact (`BOTJI_ARTIFACT_MAX_BYTES`)
- Agent max turns: 60 (`agent.max_turns` in `config.yaml`)
- Compression threshold: 65% of context window
- Healthcheck interval: 60 s
- Docker log retention: 5 × 50 MB rotating files

If you see sustained RSS > 1 GB the most likely cause is artifact retention.
Older artifacts under `/opt/data/artifacts/` can be archived offline; see
`scripts/backup-cloud.sh` and `scripts/snapshot.sh`.
