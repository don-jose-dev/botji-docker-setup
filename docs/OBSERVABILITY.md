# Observability

`botji-core` exports Prometheus metrics over HTTP so production can graph
delivery gate verdicts, artifact transform performance, and tool error rates
per tenant. This is the "observability" pillar of the enterprise-quality
plan; before this, the only post-deploy signal was a log scan in
`scripts/vps-postdeploy-smoke.sh`.

## Endpoint

The metrics server runs **inside the Hermes container** as a sidecar HTTP
server on a separate port (the Hermes gateway does not host plugin-registered
HTTP routes today).

| Setting | Default | Override |
|---|---|---|
| Bind address | `127.0.0.1` (loopback only) | `BOTJI_METRICS_BIND` |
| Port | `9090` | `BOTJI_METRICS_PORT` |
| Path | `/metrics` | n/a (fixed by `prometheus_client`) |

The server is started from `seed/hermes/plugins/botji-core/__init__.py:register()`
via the `metrics/exporter.py` module. It is **fail-open**: if the optional
`prometheus_client` dep is missing, or the port is in use, the plugin logs a
warning and continues to serve tools normally. Metrics absence must never
break production.

The server is **loopback-only by default**. To scrape from outside the
container, either:

1. Add a Docker host port mapping to `docker-compose.yml`, e.g.
   `127.0.0.1:9090:9090`. Keep it bound to host loopback — there is no auth
   on the endpoint, by design (Prometheus scrape model).
2. Run the Prometheus scraper inside the same Docker network and target
   `<tenant>-hermes:9090`. This is the preferred production shape — it never
   exposes metrics to the public internet.

## Metrics

| Name | Type | Labels | What it counts |
|---|---|---|---|
| `botji_delivery_gate_total` | counter | `verdict`, `tenant` | `delivery_gate` tool verdicts. `verdict` ∈ `{clear, warn, block, unknown}`. |
| `botji_artifact_transform_total` | counter | `operation`, `tenant` | `artifact_transform` calls. `operation` ∈ `{edit_image, exact_copy, render_schema, other}`. |
| `botji_artifact_transform_duration_seconds` | histogram | `operation`, `tenant` | Wall-clock duration of `artifact_transform` from pre- to post-hook. Buckets: 1, 5, 10, 30, 60, 90, 120, 180, 300 s. |
| `botji_receipt_record_total` | counter | `status`, `tenant` | `receipt_record` outcomes. `status` ∈ `{pass, warn, block, unknown}`. |
| `botji_tool_error_total` | counter | `tool`, `error_type`, `tenant` | Tool calls that returned `{"success": false}`. `error_type` is the first token of the error string (~Python class name). |
| `botji_container_info` | gauge (=1) | `tenant`, `image_digest`, `started_at` | Static container metadata. Useful for templating and joins. |

The `tenant` label is read once from `BOTJI_TENANT_ID` at plugin load and is
constant for the process lifetime.

## Scraping

Sample Prometheus snippet (one job per tenant if scraping from outside the
container network):

```yaml
scrape_configs:
  - job_name: botji
    scrape_interval: 30s
    metrics_path: /metrics
    static_configs:
      - targets:
          - botji-hermes:9090
        labels:
          tenant: botji
      - targets:
          - botji-degain-hermes:9090
        labels:
          tenant: degain
```

The `tenant` Prometheus label here is the one Prometheus tags onto scraped
series. It does **not** replace the `tenant` label baked into each series by
the exporter; the two should agree, but the exporter's label is what the
dashboard uses.

## Importing the dashboard

```sh
# Grafana 10+
curl -sX POST \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -H "Content-Type: application/json" \
  -d @scripts/dashboards/botji-overview.json \
  https://grafana.example.com/api/dashboards/db
```

Or use the Grafana UI: **Dashboards → Import → Upload JSON file** and pick
`scripts/dashboards/botji-overview.json`. The dashboard expects a Prometheus
data source available as `${DS_PROMETHEUS}` — Grafana will prompt you to
select one on import.

Panels:

1. **Delivery gate verdict rate** — stacked area, verdicts / sec
2. **artifact_transform p95 duration** — line, with 90s / 180s threshold bands
3. **Tool error rate by tool and error_type** — line per (tool, error_type)
4. **Container restarts (distinct started_at)** — line, restart spikes
5. **Receipt status rate** — stacked area, upstream signal to delivery gate
6. **Container info** — table of currently-reporting containers

The dashboard is templated by `tenant`, queried from
`label_values(botji_container_info, tenant)`. Multi-select is enabled.

## LLM-judge eval costs (out-of-band)

The skill-interaction LLM-judge tier (`evals/skill_interactions/`) is
**not** wired into the Prometheus exporter today — it runs as a
`workflow_dispatch` CI job and prints estimated cost per run (~$0.05/fixture,
20-fixture cap by default). When the judge moves to blocking, OpenAI
spend will move into this observability surface as a counter labeled by
`tenant` and `verdict`. See [`evals/skill_interactions/README.md`](../evals/skill_interactions/README.md)
for the current cost model.

## Caveats

- **Cardinality**: label values are bounded by the cardinality table at the
  top of `seed/hermes/plugins/botji-core/metrics/exporter.py`. Do not add
  session IDs, user IDs, file paths, prompts, or anything user-supplied.
  Raising the cardinality contract is a charter-relevant change.
- **Retention**: `prometheus_client` keeps counters in memory; restarts reset
  them to zero. Use Prometheus' own storage for history. The `_created`
  series exported alongside each counter lets PromQL detect counter resets.
- **Sensitive data**: no message text, user identifiers, or file contents
  ever land in a label. `error_type` is the first colon-delimited token of
  the error string (typically a Python class name), capped at 64 chars.
  Image digests in `botji_container_info` are container-level, not artifact
  content hashes.
- **Fail-open**: if `prometheus_client` is not installed, or the bind fails,
  the plugin logs `botji-core metrics: ...` at WARN level and continues.
  Verify with: `docker logs <tenant>-hermes 2>&1 | grep 'botji-core metrics:'`.
- **Histogram vs summary**: `artifact_transform_duration_seconds` is a
  histogram so `histogram_quantile()` aggregates across tenants. Buckets
  match the existing budget config (tool cap 90s, response cap 180s).
- **Tool error vs verdict**: a `delivery_gate` call that failed
  (`success: false`) increments `botji_tool_error_total{tool="delivery_gate"}`
  but does **not** increment `botji_delivery_gate_total` — only successful
  gate calls count toward verdicts.

## Adding a new metric

The metrics module counts toward the `botji-core` LOC cap in
[`docs/BOTJI_CORE_CHARTER.md`](BOTJI_CORE_CHARTER.md). New metrics belong
here only if they are **mechanical observations** of substrate behavior
(counts, durations, verdicts). Anything that requires interpreting tool
arguments or results belongs in a skill, not here.

To add one:

1. Add the metric instance in `metrics/exporter.py:_build()`.
2. Wire it from `metrics/hooks.py:on_post_tool_call` (or a new hook).
3. Add a row to the metrics table above.
4. If the cap is breached, edit `docs/BOTJI_CORE_CHARTER.md` per the
   "Raising the LOC cap" procedure.

## Audit log

Prometheus counters are aggregate. Auditors need a **per-event** record
of every substrate tool call: who, when, what tool, what shape of args,
what outcome. The audit log is the answer.

The same `post_tool_call` hook that updates counters also appends one
JSONL line to a per-tenant append-only file. Audit failures are
swallowed — the underlying tool call is unaffected (same fail-open
contract as the metrics export).

### Tools audited

| Tool | Why |
|---|---|
| `artifact_register` | new artifact in the index |
| `source_register` | new `src_*` artifact tied to a turn |
| `artifact_write` | new `out_*` artifact written from a path |
| `receipt_record` | new `rcpt_*` receipt persisted |
| `delivery_gate` | mechanical verdict on a receipt |
| `artifact_transform` | edit/copy/render operation, often expensive |
| `artifact_review` | review action that affects downstream delivery |

This list lives in `_AUDIT_TOOLS` at the top of
`seed/hermes/plugins/botji-core/metrics/hooks.py`. Adding a new substrate
tool here means adding its name there too.

### Record schema

One JSONL line per call. Fields:

| Field | Type | What it means |
|---|---|---|
| `timestamp` | ISO-8601 string, UTC, `Z` suffix | Wall-clock at hook fire (post-tool) |
| `tool` | string | The tool name, e.g. `artifact_register` |
| `tenant` | string | `BOTJI_TENANT_ID` at plugin load |
| `session_id` | string | Hermes session id; empty when not supplied |
| `args_hash` | hex sha256 (64 chars) | Hash of the args dict (sorted keys, `default=str`). Lets auditors detect re-runs of identical calls without storing the args themselves. |
| `args_kinds` | object `{name: type-name}` | Per-field type of every arg. Auditors see *what* was passed without leaking *values*. |
| `result_status` | `"success"` or `"error"` | Derived from `parsed.get("success")` |
| `result_summary` | string ≤ 200 chars | Redacted JSON-serialized result, truncated. See "Redaction" below. |
| `duration_ms` | non-negative int | Wall-clock pre→post duration. `0` when pre-hook didn't run (e.g. unit-test direct invocations of the post hook). |

Example line (one JSON object per line, no pretty-printing):

```json
{"timestamp":"2026-05-22T17:00:00Z","tool":"artifact_register","tenant":"botji","session_id":"sess-abc","args_hash":"7f0b...","args_kinds":{"path":"str","role":"str","current_turn_id":"str"},"result_status":"success","result_summary":"{\"success\": true, \"source\": {\"artifact_id\": \"src_...\"}}","duration_ms":12}
```

### File location

```
${HERMES_HOME}/audit/<tenant>-substrate.jsonl
```

`HERMES_HOME` defaults to `/opt/data`, so on a stock VPS deploy the path
is `/opt/data/audit/botji-substrate.jsonl` (and `/opt/data/audit/botji-degain-substrate.jsonl` for the second tenant). Each container runs in its own tenant directory, so the
`<tenant>-` prefix is mostly cosmetic — but it makes accidental shared
storage configs visible at a glance.

Mode: `O_APPEND | O_CREAT`. Idempotency is enforced inside the process
via an in-memory `(tool_call_id, tool_name)` set, so re-firing the hook
on the same call (which Hermes shouldn't do) will not double-write.

### Redaction

The `result_summary` is built from the JSON-serialized result, capped at
200 characters, with the following regex patterns stripped before write:

| Pattern | Replacement | Why |
|---|---|---|
| `tok_[A-Za-z0-9_-]+` | `<redacted:secret>` | API-style opaque tokens |
| `sk_[A-Za-z0-9_-]+` | `<redacted:secret>` | OpenAI / Stripe-style secret keys |
| `Bearer\s+[A-Za-z0-9._-]+` | `<redacted:secret>` | HTTP Authorization headers |
| `eyJ[A-Za-z0-9._-]+` | `<redacted:secret>` | JWT prefix (`{"alg":...` base64url) |

Patterns live in `_SECRET_RE` at the top of `metrics/hooks.py`. The list
is intentionally conservative; when in doubt the redactor drops the
substring. The audit log must never become a secret-exfil channel.

`args` are *never* serialized into the record — only `args_hash` and the
per-field type via `args_kinds`. That means a `path` arg pointing to
`/tmp/incoming/customer-photo.jpg` produces `args_kinds.path = "str"`
and contributes to the hash, but the path string itself never lands on
disk in the audit log.

### Retention

The audit writer does **not** rotate the file. That's the host's job —
specifically, `logrotate` on the VPS (or whichever log-rotation mechanism
the host uses). The expected configuration on a Debian/Ubuntu VPS:

```
/opt/data/audit/*-substrate.jsonl {
    daily
    rotate 90
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

`copytruncate` is the important bit: the Hermes process holds the file
descriptor open with `O_APPEND`, so a `rename`-based rotation would
silently send appends to a freed inode. `copytruncate` copies the file
aside, then truncates the original in place, so the open fd continues
to append at offset 0 in the rotated tail.

90-day retention is a reasonable starting point — long enough to debug
incidents that span weeks, short enough that storage doesn't grow without
bound. The audit JSONL averages ≤ 1 KB / call; even at 10k calls/day
per tenant that's ~900 MB per quarter compressed. Adjust as warranted.

If the file grows past ~100 MB before a daily rotation fires, that's a
signal to either (a) rotate hourly, or (b) audit fewer tools (drop one
of the `_AUDIT_TOOLS` entries). Don't quietly raise the file-size limit
without revisiting the retention model.

### Querying

The file is JSON Lines: one valid JSON object per line. Standard tools
work directly:

```sh
# Count successful delivery_gate calls in the last 24h
grep '"tool":"delivery_gate"' /opt/data/audit/botji-substrate.jsonl \
  | grep '"result_status":"success"' | wc -l

# All errors for a given session, with full record:
grep '"session_id":"session-abc"' /opt/data/audit/botji-substrate.jsonl \
  | jq 'select(.result_status == "error")'

# args_hash uniqueness — detect re-runs:
jq -r '"\(.tool) \(.args_hash)"' /opt/data/audit/botji-substrate.jsonl \
  | sort | uniq -c | sort -rn | head -20

# All tool calls slower than 5 seconds:
jq 'select(.duration_ms > 5000)' /opt/data/audit/botji-substrate.jsonl
```

The `args_hash` is reproducible: two identical calls produce the same
hash, so you can fingerprint a re-run without seeing the args.

### Fail-open caveat

The audit hook lives inside `metrics/hooks.py:on_post_tool_call`, which
is only registered when the Prometheus exporter starts (see
`__init__.py`). If `prometheus_client` is unavailable in the runtime,
both metrics and audit are dark — the substrate continues to serve tools
without observability. Production containers install
`prometheus-client` (see `Dockerfile`); CI installs it before running
the harness or stubs the metric singletons in the `audit` test suite.

Within the hook, any error in the audit path (open failure, JSON
encode failure, full disk) is caught and logged at DEBUG level. The
tool's own result reaches the caller unchanged. The `audit` harness
suite includes a `fail_open_when_write_breaks` fixture that points
`HERMES_HOME` at a non-directory and asserts the hook still returns
cleanly.


