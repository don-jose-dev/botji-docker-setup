# Launch Readiness

What exists today, what's missing, and what the user must decide before flipping public switches.

## What exists (verified 2026-05-22)

### Identity and authorization
- `seed/hermes/plugins/botji-allowlist`: file-backed Telegram allowlist with hot-reload (no container recreate to add a user). Per-tenant, env-fallback for bootstrap.
- Both tenants have working allowlists in production.
- Test coverage: `tests/fixtures/allowlist/*.yaml`.

### Onboarding
- `seed/hermes/skills/botji-welcome`: cold-start "hi" handler with inline-button affordance on Telegram. Routes to render workflows based on user choice.

### Rate limiting (new in this PR)
- `seed/hermes/plugins/botji-rate-limit`: in-memory sliding window per Telegram user.
- Configurable via env: `BOTJI_RATE_LIMIT_PER_MIN` (default 30), `BOTJI_RATE_LIMIT_PER_HOUR` (default 200).
- Fail-open on any error.
- Test coverage: `tests/fixtures/rate-limit/*.yaml`.

### Audit log
- `seed/hermes/plugins/botji-observability/hooks.py`: append-only JSONL via `post_tool_call`.
- Records timestamp, tool, tenant, session_id, args_hash, args_kinds, result_status, redacted result_summary, duration_ms.
- Secrets regex-redacted before write (`tok_*`, `sk_*`, `Bearer *`, JWT `eyJ*`).
- Same fail-open contract as metrics.

### Delivery gate
- `seed/hermes/plugins/botji-guards/delivery_check.py` via `transform_llm_output`.
- Two failure modes: no receipt, blocked receipt. (Stale `art_*` lineage path removed in V1R PR 11 with the legacy plugin.)
- Fail-open (Hermes contract).

### Health checks
- `scripts/vps-postdeploy-smoke.sh` runs after every deploy.
- Budget scoped to container restart age (PR #19).
- Both tenants verified green after every merge in the current deploy chain.

## What's missing (genuine gaps)

### Error budget alerts
- No alertmanager in compose today.
- No alert configuration files.
- **Plan:** Grafana + Alertmanager added to compose as a post-V1R track (see `docs/BOTJI_V1R.md` "Post-V1R tracks"). Open-source-in-compose only.
- **Workaround until then:** the audit log's JSONL output is grep-able for failure patterns; a cron job could parse it and ping a webhook. Not built tonight.

### Public-mode toggle
- Currently the allowlist is mandatory — every Telegram user must be on the list.
- No "anyone can use it" mode exists.
- **Decision required from user:** truly public, or invite-only?
- If public: needs an allowlist bypass mechanism (env flag, e.g. `BOTJI_ALLOWLIST_BYPASS=1`) which is a small follow-up PR.
- If invite-only: current state is correct; just need to publish how to request access.

### DR backup
- No backup pipeline in production today.
- **Plan:** restic to a second mounted volume on the VPS (post-V1R track in `docs/BOTJI_V1R.md`).

### Local LLM judge
- `evals/skill_interactions/judge.py` currently uses raw `openai.OpenAI()` — broken without an API key (which is prohibited under the no-new-paid-services constraint).
- **Plan:** rewrite to use Hermes's Codex bridge (`ctx.llm.complete_structured` with `allow_provider_override=true, allowed_providers=[openai-codex]`) per the Hermes audit (PR #44). Tracked as a separate task.

## What's required before flipping public switches

### User decisions (engineering can't decide)
- [ ] Public Telegram bot handle (or invite-only handle).
- [ ] Domain pointed at the VPS (or a GitHub Pages site for docs in the interim).
- [ ] License confirmation (BUSL 1.1 default unless overridden).
- [ ] Early-access form provider (Tally / Plausible / etc.).
- [ ] Allowlist policy: open mode (needs bypass flag), invite-only (current), or hybrid.

### Engineering checks (all green right now)
- [x] Allowlist works (tested).
- [x] Rate limit works (tested in this PR).
- [x] Welcome flow works (production).
- [x] Delivery gate works (tested).
- [x] Audit log works (tested).
- [x] Smoke checks green on both tenants.

## Recommended pre-launch sequence

1. User decides public-mode policy. If open mode is wanted, ship a `BOTJI_ALLOWLIST_BYPASS=1` flag in a small follow-up PR.
2. User decides public name and domain. Update `docs/PUBLIC.md` placeholders. Update DNS.
3. User confirms license. Add `LICENSE` file at repo root.
4. Watch metrics / audit log for 24h after first public hit. The audit JSONL + rate-limit log give visibility into who's calling what.
5. Tune `BOTJI_RATE_LIMIT_PER_MIN` and `BOTJI_RATE_LIMIT_PER_HOUR` if defaults turn out wrong for real traffic.

## Tuning defaults

The rate-limit defaults (30/min, 200/hour) assume each render request is a few messages. If the workflow is one-message-one-render, those are generous. If the workflow involves heavy back-and-forth, raise the per-hour limit. The audit log will show actual usage shapes within hours of launch.
