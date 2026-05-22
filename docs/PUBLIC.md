# Botji

**An audited-workflow harness for AI agents, running on [Hermes](https://github.com/don-jose-dev/hermes-agent).**

Agent runs are recorded as workflows: source → evidence → transform → review → receipt → delivery. Each step is durable. Each transition has a gate. The harness refuses to deliver an output that fails review.

## See it running

The render bot is the live example. Send a brief or a sketch via Telegram, get back an interior render. The whole pipeline — source registration, manifest extraction, render generation, 8-axis fidelity review, receipt — runs through Botji's harness primitives.

> **Telegram:** _bot handle TBD — fill in at launch_

## Why audited workflows

Agents are stochastic. Production systems need to know what an agent did, why it did it, and whether the output meets the contract. Botji turns each agent run into a sequence of typed, recorded steps with hard gates between them. If a step fails review, delivery blocks — the user doesn't see the broken output, and the operator has a durable trail.

This is what the [charter](BOTJI_CORE_CHARTER.md) calls "the audited-workflow harness on Hermes."

## Architecture in one paragraph

Hermes runs the agent loop, tools, skills, plugins, and orchestration. Botji sits on top as a set of Hermes plugins and skills: `botji-core` records facts (sources, outputs, evidence, receipts), per-file-type adapters extract evidence, `botji-review` evaluates outputs against declarative axes, `botji-render` runs generation. Skills decide which workflow runs and how the brief is shaped. The [charter](BOTJI_CORE_CHARTER.md) is the line between mechanical substrate and semantic skill responsibility — and it's CI-enforced via a LOC cap so core can't silently grow into a god plugin.

## Open source and self-hostable

Everything runs in `docker-compose` on a single VPS. No new vendors beyond what your workflows require (the render bot uses Codex for image generation; everything else is local). No SaaS lock-in. The same image runs as many tenants as you want under `/opt/<tenant>/`.

> **License:** _source-available, exact terms TBD before launch (BUSL 1.1 or similar)_

## Roadmap

See [V1R](BOTJI_V1R.md) for the current implementation rewrite — 12 PRs to drain the legacy `botji-artifacts` plugin into the clean adapter/review/render shape with native IDs only.

After V1R lands:

- Typed manifests end-to-end (Phase C of the [Hermes-native rewrite plan](HERMES_NATIVE_REWRITE_PLAN.md))
- Local LLM judge for evals (Ollama in compose, no API key)
- Grafana + Alertmanager (open-source observability stack)
- Source-bound image edit (requires upstream Hermes contribution to `ImageGenProvider`)
- DR backup via restic to a second mounted volume

## Early access

> **Form:** _TBD — Tally / Plausible form embed at launch_

If you're building agent products and have an opinion on workflow audit, durable receipts, or declarative review axes, get in touch. The harness is usable today; the V1R rewrite makes it sharper.

## Status

- ✅ Render bot live on Telegram (two tenants)
- ✅ Hard delivery gate wired and blocking
- ✅ Append-only audit log producing JSONL
- ✅ Charter and V1R plan public
- ⏳ V1R rewrite in progress (12-PR sequence)
- ⏳ Local LLM judge (post-V1R)
- ⏳ Observability stack (post-V1R)
