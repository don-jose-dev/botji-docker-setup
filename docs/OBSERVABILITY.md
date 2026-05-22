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
