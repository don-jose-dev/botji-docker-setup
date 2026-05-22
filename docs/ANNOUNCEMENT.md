# Botji announcement copy (drafts)

All copy below is **draft for user review**. Nothing is published until explicit approval. The PUBLIC.md one-pager is the authoritative content source; this file is the marketing surface.

## Short — X / Mastodon (under 280 chars)

```
Botji: an audited-workflow harness for AI agents.

Every step recorded. Every output gated by review. Open-source-in-compose, self-hostable on one VPS.

Live example: a render bot you can try on Telegram.

→ [link]
```

## Medium — Hacker News / dev forums

**Title:** Show HN: Botji – an audited-workflow harness for AI agents

**First paragraph:**

I built Botji to solve a problem I kept running into with agent products: how do you know what the agent actually did, and how do you stop it from shipping broken outputs? Botji turns agent runs into typed workflows with durable records and hard delivery gates. Each step — source → evidence → transform → review → receipt → delivery — is recorded. If a step fails review, delivery blocks. The whole thing runs in `docker-compose` on one VPS, no new API keys beyond what your workflows need.

**Second paragraph:**

The live example is a render bot — send a brief or a sketch on Telegram, get back an interior render that's been through an 8-axis fidelity review (lineage, byte-exact fidelity for copies, provider-transform proof for edits, visual fidelity against the source, brief alignment, etc.). Source-available. The V1R rewrite is in progress: 12 PRs to drain the legacy implementation into a thin adapter/review/render shape with native IDs only.

**Third paragraph (technical bait):**

Built on Hermes (the agent runtime). Botji is ~9k LOC of plugins and skills on top. Hermes runs the agent loop, tools, skills, plugins, and orchestration; Botji adds the contract that turns agent runs into auditable workflows. The split is enforced — the charter at `docs/BOTJI_CORE_CHARTER.md` defines what's allowed in the mechanical substrate vs what belongs in skills, and CI parses a LOC cap from the charter to prevent silent growth.

## Long — blog post (placeholder)

To be drafted after:
- V1R PR 11 lands (rewrite done)
- Live Telegram handle confirmed
- Public name and domain finalized
- License confirmed

Outline:
1. The problem with agent products that ship without audit (concrete failure mode)
2. What an "audited workflow" actually means (typed steps, durable records, hard gates)
3. Why a thin substrate + adapters + declarative review (vs. a god class)
4. The Hermes split (Hermes runs the machine, Botji owns the contracts)
5. The V1R rewrite as a discipline exercise (ship-with-consumer, hard cutover, no scaffolds)
6. What's next (typed manifests, local judge, source-bound edit upstream)

## Key claims that must be true at launch

- [x] Render bot is live on Telegram and produces a render end-to-end
- [x] Charter is published in the repo
- [x] V1R plan doc is published in the repo
- [x] `delivery_gate` is wired and actually blocks bad outputs
- [x] Audit log is producing JSONL records
- [x] Both tenants are healthy
- [ ] Public Telegram handle confirmed
- [ ] Domain pointed at the VPS
- [ ] License chosen and `LICENSE` file in the repo
- [ ] Early access form live

All checked items are true today (2026-05-22). Unchecked items are launch-gate decisions only the user can make.

## What not to say

- Don't claim "production-ready" — V1R is in flight; honesty is the brand.
- Don't claim "fully open source" — license is source-available, not OSI-open.
- Don't claim parity with Hermes — Botji depends on Hermes, doesn't replace it.
- Don't claim "agentic engineering" or "AGI" framing — concrete claims only.
