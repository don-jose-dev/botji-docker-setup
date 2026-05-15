# AGENTS.md — Botji Workspace Rules

This workspace is controlled by Botji.

## Source-of-truth order

1. Current Prompt Contract.
2. Explicit user-provided files and command outputs.
3. Repository tests, schemas, and build results.
4. Botji skills and persistent memory.
5. Inference.

## Rules

- Do not act outside the Prompt Contract.
- Preserve items in the preserve list.
- Only change items in the change list.
- Do not claim verification unless a verification command actually ran.
- Do not read `.env`, auth files, private keys, token stores, or unrelated user files unless explicitly approved.
- Do not run destructive commands such as `rm -rf`, force-push, credential export, or broad chmod/chown without explicit approval.
- Prefer small patches, tests, and clear rollback notes.
- Return commands run, files changed, assumptions, and remaining risks.
