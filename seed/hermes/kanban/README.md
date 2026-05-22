# seed/hermes/kanban/

Hermes Kanban workflow templates seeded into each tenant's `$DATA_DIR/kanban/`.

## What lives here

`workflows/` — declarative templates for multi-step Kanban tasks. Each file
describes the steps, transitions, retry budget, and fallback behaviour for
one durable flow.

| Template | What it covers |
|---|---|
| `workflows/render_retry.yaml` | Source-bound render: manifest extract → transform → review → 1 retry → deliver or escalate. Mirrors the canonical `1 + 1` retry shape from `botji-render-mode`. |

## Schema status

Hermes Kanban v1 reserves two nullable columns on `tasks` for forward-compat
workflow routing — `workflow_template_id` and `current_step_key` (see
`C:/Dev/hermes-agent/hermes_cli/kanban_db.py:841-845` and the v1 spec at
`website/docs/user-guide/features/kanban.md#forward-compatibility`). The v1
kernel writes these columns but does NOT yet consult them for routing.

That means a template here is, today, primarily a **machine-readable contract
for the skill-following agent** — it points the model at the durable shape
without scattering retry prose across every skill. When Kanban v2 ships the
dispatcher routing, the dispatcher will consume the same files.

If upstream Hermes lands a template format that diverges from the shape used
here, this directory is rewritten — no migration penalty, because no external
code consumes these files yet beyond the skill instructions and the
deterministic harness fixture (`tests/fixtures/render/render_retry_template.yaml`).

## Seeding

`scripts/deploy-tenants.sh::sync_code_components` copies this entire tree to
`$DATA_DIR/kanban/` on every deploy. Idempotent — safe to re-seed. Owned by
`$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID`.

## Fallback contract

Every template documents a `fallback:` block. If a tenant has
`kanban.dispatch_in_gateway: false` in `config.yaml`, or the Kanban DB is
unreachable, the skill-following agent reads the same template intent from
the prose in the matching `SKILL.md` and runs the retry inline. Skill
behaviour is unchanged when Kanban is off — the templates are the durable
layer, the prose is the always-on fallback.

## See also

- `docs/OPERATIONS.md` — "Kanban-backed render retry" runbook: inspecting,
  manually advancing, and cancelling stuck chains.
- `seed/hermes/skills/botji-2d-to-3d/SKILL.md` Step 5 — the matching prose
  for source-bound sketch-to-render flows.
- `seed/hermes/skills/botji-codex-engineering/SKILL.md` 2-attempt mutation
  section — the engineering-side cross-reference.
- `seed/hermes/skills/botji-render-mode/SKILL.md` Retry budget — the
  canonical `1 + 1` cap referenced by all of the above.
