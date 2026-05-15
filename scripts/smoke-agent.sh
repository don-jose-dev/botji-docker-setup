#!/usr/bin/env bash
set -euo pipefail

docker compose --env-file .env up -d hermes
echo "Waiting for gateway health..."
for i in {1..30}; do
  if curl -fsS "http://127.0.0.1:${HERMES_API_PORT:-8642}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

docker compose --env-file .env exec hermes bash -lc '
  set -euo pipefail
  echo "Hermes status:"
  hermes status || true
  echo "Codex status:"
  codex --version
  echo "Botji protocol files:"
  ls -la /opt/data/skills/botji-prompt-contract /opt/data/schemas /opt/data/.codex
'

cat <<'EOF'
Agent smoke finished.

Now send this to Telegram:
  protocol self-test: answer in Botji substantive tier. Show Prompt Contract, steps, and review footer. Do not execute tools.

Expected shape:
  - visible Prompt Contract or compact contract card
  - step list
  - answer
  - review footer with per-axis statuses
EOF
