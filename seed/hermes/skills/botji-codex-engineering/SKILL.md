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
