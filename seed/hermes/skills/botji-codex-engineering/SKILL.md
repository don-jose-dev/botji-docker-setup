---
name: botji-codex-engineering
description: Engineering tasks via Codex — repo inspection, code changes, tests, builds, Docker, scripts. Passes Prompt Contract into every task; treats Codex output as draft evidence.
tags:
  - botji
  - codex
  - engineering
---

# Botji Codex Engineering Skill

Use this skill when the task involves repository work, tests, builds, scripts, Docker files, generated code, or structured engineering output.

## Roles

Hermes:
- user-facing operator
- contract builder
- reviewer
- approval gate

Codex:
- engineering worker
- repo inspector
- code/test/build executor

Codex output is not final. It is evidence that Hermes must review.

## Tool wrappers

- `botji-codex "<task>"`: workspace-write mode inside `/workspace`
- `BOTJI_CODEX_MODE=readonly botji-codex "<task>"`: inspect-only mode
- `botji-codex-review "<review task>"`: runs Codex read-only with the source-fidelity review schema
- `botji-validate-review /path/review.json`: validates review JSON

## Required task prompt to Codex

Every Codex task must include:

1. Prompt Contract ID and version.
2. Source authority list.
3. Preserve list.
4. Change list.
5. Definition of done.
6. Verification commands.
7. Forbidden actions.
8. Expected output artifact path.

## Fidelity requirements for Codex evidence

- Pass the Prompt Contract into the Codex task.
- Capture Codex's visible model/provider/sandbox line when present.
- If Codex uses a different model/provider/profile than the contract expected, disclose it as a fidelity event; do not hide it.
- If Codex fails before running the requested inspection or tests, do not summarize the repo/workspace as inspected.
- Exact sandbox/bootstrap failures such as `bwrap: No permissions to create a new namespace` make source_coverage `missing` or `partial`, not `match`.
- Treat Codex output as draft evidence; Hermes must still run the source-fidelity review.
- For tool-backed engineering responses, persist the Prompt Contract and review JSON when file tools are available.

## botji_render and the Responses API flow

### botji_render vs artifact_transform

| Tool | When to use |
|---|---|
| `artifact_transform(operation="edit_image")` | Source-bound image work — a source artifact must be registered and passed as `input_image`. PRIMARY for photo→3D and all fidelity-preserving edits. |
| `botji_render` | Rendering a schema or spec into an image when there is no pixel source to preserve (e.g. a DXF schema → styled render, or a spec-only brief). Calls the Responses API internally. |

Do not use `botji_render` when the user provided a source image. Use `artifact_transform` instead to preserve the source pixel input.

### Image generation via Responses API

Image generation goes through the Codex Responses API with the `image_generation` tool. The flow:

1. Hermes calls `artifact_transform` or `botji_render` with a structured brief.
2. The plugin sends the brief + source image (if any) to the Responses API `image_generation` tool.
3. Codex returns the generated image; the plugin registers it as an output artifact.
4. `artifact_review` compares the output against the source artifact and hard requirements.

### 2-attempt mutation strategy (1 + 1 retry, canonical)

The canonical cap is in `botji-render-mode` (Retry budget): max 2 transforms per turn. On a failed or low-fidelity generation, the plugin retries once with progressively tighter constraints — not looser ones. Quality degrades gracefully:

- Attempt 1: full brief with preferred camera/mood.
- Attempt 2 (retry, on block): both expand the FORBIDDEN list with what the first attempt hallucinated AND simplify the scene (strip non-essential elements, harden object count rules) — combine both moves into the single retry, don't burn another slot.

After the retry, if still blocked, stop and report. Do not silently accept a low-fidelity output and do not run a third transform without explicit user authorisation.

### user_id passthrough

Every `artifact_transform`, `botji_render`, and `image_generate` call must pass `user_id` through to the plugin. This is required for fair-share queueing — the plugin rate-limits per user. Omitting `user_id` breaks queue fairness and may cause incorrect billing attribution.

## Default safety

- Codex works in `/workspace`.
- No network access by default in workspace-write profile.
- Do not mount Docker socket unless the task explicitly requires container builds and the user accepts the risk.
- No destructive git operations unless approved.
- Do not read `.env`, auth files, private keys, or token files unless explicitly part of the approved task.
- Do not silently fallback models/providers.

## Failure handling

When a Codex run fails:

1. Quote the exact failing command/tool stage.
2. Quote the exact error line(s), redacted if necessary.
3. State what was not inspected or verified.
4. Lower the relevant review axes.
5. Offer the smallest safe remediation.
6. Do not claim verification.

## Example

```bash
botji-codex "Prompt Contract: prompt-20260515-v1
Task: inspect this repo and propose the smallest Docker fix.
Preserve: existing API, existing tests.
Change: Dockerfile and compose only.
Verification: docker compose config, run tests if present.
Forbidden: deleting files, changing secrets, network access beyond configured sandbox.
Return: concise patch summary and commands run."
```
