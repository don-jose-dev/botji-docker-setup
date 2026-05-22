# Botji Harness — Long-Term Plan

## What Botji is becoming

An **audited-workflow harness on [Hermes](https://github.com/don-jose-dev/hermes-agent)**. The render bot is one workflow. The harness is the product. Adding the second workflow should be a day of YAML and one new skill, not a multi-week refactor.

This file is the contract for the work after [V1R](BOTJI_V1R.md). The phases below stack — each phase's done state is required to begin the next. No phase ships scaffolds; every phase produces one observable user-side change before it can be declared done.

## Cross-cutting design constraint

**Typed schema at every boundary.** Three boundaries exist:
- **Inbound** — LLM → tool. Every Botji tool input is a Pydantic v2 model with explicit constraints and auto-correction for known-recoverable mistakes. Validation errors return `{error, field, got, expected}` so the LLM can self-correct on the next turn.
- **Outbound** — provider response → Hermes. Every provider response is coerced to a typed model at the adapter boundary. Downstream consumers receive guaranteed-shape data. No defensive `isinstance` checks at consumption.
- **Inter-step** — skill → tool, tool → skill, step → receipt. Receipts, evidence, manifests are typed entities with versioned schemas.

This is the structural fix that prevents the entire class of bug observed in the 2026-05-22 retrospective (Hermes Codex `.strip()` on a list, `artifact_transform` bounding box with `y1 < y0`). Every phase below applies this pattern to a different surface.

## Phases

### Phase 1 — V1R rewrite (3 weeks, in flight)

Per [`BOTJI_V1R.md`](BOTJI_V1R.md). 12 PRs that drain `botji-artifacts` and produce the clean adapter/review/render shape with native IDs only.

**Done state:**
- `seed/hermes/plugins/botji-artifacts/` does not exist
- Charter LOC cap drops 1100 → ~600
- All workflows running on native IDs
- 49/49 fixtures green on both tenants

### Phase 2 — Typed boundaries (3 weeks, gated on Phase 1)

Apply the cross-cutting design constraint everywhere.

**Work units (one PR each):**
- Botji-side: Pydantic input model for `artifact_transform` (with `Rect` auto-normalize for reversed boxes; closes the degain `y1 < y0` class)
- Botji-side: Pydantic input models for the remaining substrate tools (`source_register`, `output_write`, `evidence_record`, `receipt_record`, `delivery_gate`)
- Botji-side: tool spec emitted to Hermes is generated from the Pydantic models (so the LLM sees constraints upfront)
- Botji-side: validation errors return structured payloads with `{error, field, got, expected}` for LLM self-correction
- Upstream: PR against `hermes-agent` adding Pydantic response parsing to `codex_responses_adapter` (closes the botji `.strip()` on list class)
- Botji-side fallback while upstream lands: `transform_tool_result` hook in `botji-core` that recognizes the specific Hermes parse-error and rewrites it into a user-visible message + structured retry hint
- Receipt schema versioning: `rcpt_*` records carry a `schema_version` field; readers tolerate older versions; one explicit migration step per version bump

**Done state:**
- Both 2026-05-22 failure classes are structurally impossible
- Receipt schema can evolve without breaking historical records
- Every Botji tool has a Pydantic input model

### Phase 3 — Workflow definition language (3 weeks, gated on Phase 2)

Make adding a new workflow declarative instead of code.

**Work units:**
- New file shape: `seed/hermes/workflows/<name>.yaml` declaring (a) trigger (intent / keyword / skill route), (b) steps (each pointing at a skill or tool), (c) success / retry / escalate rules, (d) review axes, (e) cost ceiling per turn
- Loader that turns `workflow.yaml` into the existing skill/router/gate plumbing
- Per-tenant opt-in via `BOTJI_ENABLED_WORKFLOWS` env (or per-tenant config file)
- Eat-our-own-dogfood: re-encode the existing render bot as a `render.yaml` workflow — no behaviour change, just declarative
- Demonstration second workflow: pick one (code-review, document-summary, etc. — user decides)

**Done state:**
- Render bot runs from `workflows/render.yaml` instead of hardcoded routing
- A second workflow exists in tree and runs end-to-end on the same harness
- Adding a third workflow is a day's work (yaml + one skill), not a refactor

### Phase 4 — Observability for operators (3 weeks, gated on Phase 3)

Operator confidence: when something breaks, you know **what** + **which workflow** + **which tenant** in under 30 seconds without SSH.

**Work units:**
- Grafana + Alertmanager added to `docker-compose.yml` (open-source, $0 new vendors)
- Per-workflow per-tenant dashboards: latency p99, error rate, gate-block rate, retry rate, cost-per-turn
- SLO contracts as YAML — alert when a workflow breaches its SLO over a 10-min window
- Cost accounting per workflow per tenant (Codex token usage, Hermes wall time)
- Drift alerts: charter LOC cap breach, receipt schema gaps, audit log gaps
- Dashboard for the charter itself — current LOC, deltas per merge, history

**Done state:**
- A new operator can see the system's health without reading code
- Every SLO breach pages somewhere (Telegram, webhook, log) — your choice of channel
- Cost-per-workflow is tracked monthly with no manual work

### Phase 5 — Reliability primitives (4 weeks, gated on Phase 4)

Sleep through outages.

**Work units:**
- DR backup: restic to a second mounted volume on the VPS (and/or peer-VPS rsync). Daily snapshots. Restore-from-backup playbook in `docs/OPERATIONS.md`
- Idempotency: every tool call carries a `request_id`; retries collapse via dedup at substrate level
- Durable workflow execution: Kanban-backed (gated on Hermes upstream adding real dispatch — track in V1R PR 12-style watch; OR Botji-side file-backed queue as a fallback)
- Per-workflow tool allowlists: a workflow declares the tools it can call; substrate enforces. Closes the "any skill can call any tool" hole
- Tenant isolation: substrate-level checks prevent cross-tenant `art_*`/`src_*` access; tested via fixtures

**Done state:**
- VPS data loss is recoverable in minutes from local backup
- A retried tool call doesn't double-charge or double-execute
- A new workflow cannot accidentally call a tool it shouldn't

### Phase 6 — External author surface (optional, business-decision-gated)

**Only if the business model says workflow authors are the customer** (the "harness as developer product" path from the strategic-position thread).

**Work units (only if pursued):**
- Plugin SDK + docs for third-party workflow authors
- Workflow signing/verification (operator installs trusted-only workflows)
- Marketplace / registry — out of scope for v1

**Done state:** Botji is sellable as a developer product, not just as a render bot. User decision required before starting.

### Phase 7 — Self-test (2 weeks, gated on Phase 5)

Regressions caught before they reach a user.

**Work units:**
- `botji-harness self-check` mode that runs all axes against a known-good fixture set
- Pre-traffic gate on deploy: container refuses to start accepting messages until self-check passes
- Drift-from-charter detector in CI (charter LOC, schema versions, plugin shape) — runs nightly, opens an issue on drift
- Receipt-replay test: re-run a stored receipt's evidence axes against the current code and confirm same verdict (catches regressions in review axes)

**Done state:** every deploy has a green self-check before it can serve traffic.

## Cross-phase discipline

These rules apply to every PR in every phase:

1. **One PR per session, smoke between merges.** V1R's cadence extended forever.
2. **Typed schema at every boundary.** No new code crosses a boundary without a Pydantic model.
3. **Ship-with-consumer.** A PR merges only if the code it adds has a runtime caller in-tree at merge time. No scaffolds.
4. **Net LOC delta ≤ 0** for PRs that modify existing code. New phases that add new files have a per-phase LOC budget that must be justified in the phase intro.
5. **No new paid services.** Codex subscription only. Everything else open-source in compose.
6. **Charter is the gate.** Every PR that touches `botji-core/` justifies against the Allowed table; cap bumps require charter edit in the same PR.
7. **One observable user-side change per phase** before the phase can be declared done.

## Realistic timeline

| Phase | Weeks | Cumulative |
|---|---|---|
| 1 — V1R rewrite | 3 | 3 |
| 2 — Typed boundaries | 3 | 6 |
| 3 — Workflow definition language | 3 | 9 |
| 4 — Observability | 3 | 12 |
| 5 — Reliability primitives | 4 | 16 |
| 6 — External author surface | optional | — |
| 7 — Self-test | 2 | 14–18 |

**~3–4 months** from 2026-05-22 to a mature harness state at one-PR-per-session cadence. Calendar-paced, not crunch-paced. Each phase produces observable value on its own — there's no all-or-nothing milestone.

## Customer-visible deliverables per phase

What changes for the actual end-user of each phase:

| Phase | What the customer sees |
|---|---|
| 1 — V1R | (mostly invisible — implementation cleanup) |
| 2 — Typed boundaries | Fewer error responses; LLM self-corrects faster; reliability ratchets up |
| 3 — Workflow language | Second workflow available (render + code-review / image-edit / whatever you pick) |
| 4 — Observability | Operator (you) regains confidence to ship publicly without 24/7 watching |
| 5 — Reliability | Outages become 10-minute restores instead of overnight incidents |
| 6 — External authors | A second customer segment exists (only if you pursue this) |
| 7 — Self-test | "How do I know it's healthy?" gets a one-word answer: deploy passed |

## Locked decisions

### Phase 3 second workflow = floor-plan / spatial-extraction
**Decision date:** 2026-05-23.

**Rationale:** Same customer segment as the render bot (interior design / renovation buyers — a $153B market growing 4.4–5.8% annually with consumer AI pricing $10–$80/mo). Different modality — PDF / photo of floor plan — exercises the V1R adapter protocol on non-image-render content. Different review axes (spatial consistency: room labels, openings sane, dimensions plausible) exercise V1R PR 8's declarative-axes-in-YAML mechanism. Creates a commercial funnel where extracted floor plans feed into render-bot briefs: two-step product, one customer, one harness.

**Considered and rejected:** AI code review. The AI code review market is $400M–$2B and already won by CodeRabbit (2M repos, 13M PRs reviewed, most-installed AI app on both GitHub and GitLab), Greptile (full-codebase indexing differentiator), and Graphite's Diamond (selective low-noise positioning). Entering would fragment the customer base, fight an established 2-million-repo distribution moat, and require developer-marketing overhead a solo product owner cannot sustain.

### Phase 6 external author surface = SKIP, quarterly review
**Decision date:** 2026-05-23.

**Rationale:** The "auditable agent harness as developer SDK" space is already won by LangGraph (production standard for stateful auditable workflows with built-in checkpointing), Mastra (production at Replit's Agent 3, improving task success 80% → 96% across thousands of daily sessions), CrewAI (fastest-prototyping), and OpenAI Agents SDK (lowest-friction OpenAI-only). A solo founder cannot out-build LangChain on the same axis. Pursuing an SDK now would slow the B2C ramp the render bot already has live traction on. Solo-founder monetization research is unambiguous: B2C SaaS with hybrid pricing is the proven path (HeadshotPro $300K/mo, TypingMind multi-million ARR).

**What stays preserved:** Botji source remains source-available (BUSL 1.1 default per `LAUNCH_READINESS.md`). External devs who find the repo can fork/learn without an SDK team. Phases 4–5 build the same primitives an SDK would need, for internal use first.

**Quarterly reconsideration triggers** (next check Aug 2026, then Nov 2026, etc.). Re-evaluate Phase 6 if any are true:
- 3+ unsolicited "can I build my own workflow on this?" inquiries
- A paying customer asks for the SDK as a contract requirement
- Two workflows are shipped on the harness and a third party offers to build a fourth

If none of those triggers fire by a quarterly check, the SKIP decision auto-reaffirms.

## Still-open decisions (user-side, not blocking)

- **Phase 4 — alert channel.** Telegram self-ping vs. webhook vs. email. Open-source-in-compose works for any. Decision deferable to start of Phase 4.

## How this doc is enforced

- Every PR after V1R names which phase it belongs to in the title (e.g. `phase-2: typed input model for artifact_transform`).
- The phase's "Done state" is the gate — a phase can't be declared done until every bullet is checked.
- Cross-phase discipline rules are merge gates, not advisory.
- If a PR doesn't fit any current phase, it doesn't belong in this plan — open a separate discussion before merging.

## Companion docs

- [`BOTJI_V1R.md`](BOTJI_V1R.md) — the 12-PR implementation rewrite contract (Phase 1).
- [`BOTJI_CORE_CHARTER.md`](BOTJI_CORE_CHARTER.md) — what's allowed in the substrate vs. skills.
- [`LAUNCH_READINESS.md`](LAUNCH_READINESS.md) — pre-public-launch state and user decisions.
- [`HERMES_GAP_MATRIX.md`](HERMES_GAP_MATRIX.md) — what Hermes provides vs. what Botji must own (gating for Phase 2 upstream PRs).
