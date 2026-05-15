# Botji single-tenant Docker setup

This bundle runs Botji as one isolated tenant:

```text
Telegram → Hermes gateway → Botji contract/review skills → Codex CLI worker → reviewed reply
```

See [BOTJI_V1.md](BOTJI_V1.md) for the current generic artifact-fidelity architecture.

The important design choice is boring on purpose: one company gets one Hermes home, one Codex auth store, one workspace, and one VM/container boundary. No tenant soup. No cross-memory stew.

## What this setup gives you

- Hermes gateway with Telegram long polling by default.
- Official Hermes Docker image as the base.
- Codex CLI installed inside the Hermes container.
- Tenant-local `CODEX_HOME=/opt/data/.codex`.
- Botji `SOUL.md` that forces contract → pre-review → execution → source-fidelity review.
- Botji skills:
  - prompt contract
  - source-fidelity review
  - Codex engineering worker discipline
- JSON schemas:
  - prompt contract
  - source-fidelity review
  - artifact lineage
- Local dashboard/API bound to `127.0.0.1` on the host.
- No Docker socket mounted by default.
- No public inbound ports required for Telegram polling.

## Files

```text
.
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── Makefile
├── runtime/bin/
│   ├── botji-codex
│   ├── botji-codex-review
│   ├── botji-contract-new
│   └── botji-validate-review
├── seed/
│   ├── bootstrap-botji.sh
│   ├── codex/
│   │   ├── AGENTS.md
│   │   └── config.toml
│   ├── hermes/
│   │   ├── SOUL.md
│   │   ├── schemas/
│   │   └── skills/
│   └── workspace/README.md
└── scripts/
    ├── smoke-local.sh
    ├── smoke-agent.sh
    └── snapshot.sh
```

## Quick start

1. Copy env:

```bash
cp .env.example .env
```

2. Edit `.env`:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USERS=123456789
API_SERVER_KEY=<openssl-rand-hex-32>
```

3. Bootstrap tenant state:

```bash
make bootstrap
```

4. Configure Hermes:

```bash
make setup-hermes
```

Choose your provider/model and Telegram setup in the wizard. If you use OpenAI Codex OAuth from Hermes, keep credentials under this tenant data directory.

5. Authenticate Codex CLI:

Either use API key auth by setting `OPENAI_API_KEY` in `.env`, or run:

```bash
make codex-login
```

6. Start Botji:

```bash
make up
make logs
```

7. Open local dashboard:

```text
http://127.0.0.1:9119
```

8. Send a Telegram self-test:

```text
protocol self-test: answer in Botji substantive tier. Show Prompt Contract, steps, and review footer. Do not execute tools.
```

## Codex worker commands

Inside the running container:

```bash
botji-codex "Inspect the repo and list risky Docker issues."
BOTJI_CODEX_MODE=readonly botji-codex "Read-only repo summary."
botji-codex-review "Review the last generated answer against Prompt Contract prompt-..."
botji-validate-review /opt/data/reviews/source-fidelity-review-....json
```

## Artifact harness

Run the generic artifact adapter harness inside the container:

```bash
botji-artifact-harness --strict --require-modality-comparators
```

The harness uses deterministic local fixtures and checks register -> extract -> normalize -> schema render -> exact copy -> default exact-preserve transform -> review -> modality comparator for text, image, PDF, DXF, DOCX, XLSX, HTML, SVG, STEP, IFC, ZIP, audio, video, and binary adapters. It does not call OpenAI; real provider coverage is handled by `botji-artifact-e2e`. The `exact_copy` route validates byte-for-byte preservation by SHA-256 and is the 100% file-fidelity path; omitted `artifact_transform.operation` also defaults to exact copy.

Codex defaults:
- `botji_readonly`: read-only sandbox, no web search
- `botji_write`: workspace-write sandbox, no web search, network disabled
- credentials: `/opt/data/.codex`
- workspace: `/workspace`

## Production hardening checklist

Before selling this as “tenant-isolated,” do these:

- Pin `HERMES_IMAGE` to a tested digest or `sha-*` tag after smoke tests.
- Use one VM per tenant, not one Compose project for many tenants.
- Keep `BOTJI_DATA_DIR` and `BOTJI_WORKSPACE_DIR` unique per tenant.
- Keep `GATEWAY_ALLOW_ALL_USERS=false`.
- Keep dashboard/API bound to host loopback, or put it behind IAP/VPN/reverse proxy auth.
- Do not mount `/var/run/docker.sock` unless the task truly requires container builds.
- Snapshot `BOTJI_DATA_DIR` before upgrades.
- Run `make smoke-local` before `make up`.
- Run `make snapshot` before image upgrades.
- Never share `.env`, `/opt/data/.codex/auth.json`, or Hermes auth files.

## Important limitation

This setup makes the Botji discipline the default operating system for Hermes using `SOUL.md`, skills, schemas, and Codex wrappers. It is not a cryptographic enforcement layer. A truly hard gate would require a custom Telegram ingress/proxy or Hermes plugin that refuses outbound messages unless a valid source-fidelity review object exists. This bundle is the practical Day-1 version: strong behavioral protocol, repeatable files, schema validation helpers, and clear isolation boundaries.

## Upgrade

```bash
make snapshot
docker compose --env-file .env build --pull hermes
docker compose --env-file .env up -d hermes
make smoke-local
```

## Rollback

Set `HERMES_IMAGE` back to the previous digest/tag in `.env`, rebuild, and restore a snapshot if the data volume needs rollback:

```bash
docker compose --env-file .env build hermes
docker compose --env-file .env up -d hermes
```
