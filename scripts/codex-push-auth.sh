#!/usr/bin/env bash
# Push local Codex ChatGPT auth credentials to the VPS data volume.
#
# How to use:
#   1. On your local machine (where you have a browser):
#        npm i -g @openai/codex          # if not installed
#        codex login                      # opens ChatGPT in browser
#   2. Then run this script:
#        VPS_HOST=187.124.13.159 ./scripts/codex-push-auth.sh
#
# The credentials are copied into the VPS data volume and survive container
# restarts. No make reload is required — Codex reads the file on each call.
#
# Optional env overrides:
#   LOCAL_CODEX_HOME  — local path where codex stored auth (default: ~/.codex)
#   VPS_USER          — SSH user on VPS (default: botji)
#   VPS_HOST          — VPS IP or hostname (required)
#   VPS_DATA_PATH     — path to botji data dir on VPS (default: /opt/botji/data/botji)
#   SSH_KEY           — path to SSH private key (default: ~/.ssh/id_ed25519)
#   HERMES_UID/GID    — runtime owner inside the container (default: 10000)
set -euo pipefail

LOCAL_CODEX_HOME="${LOCAL_CODEX_HOME:-$HOME/.codex}"
VPS_USER="${VPS_USER:-botji}"
VPS_HOST="${VPS_HOST:-}"
VPS_DATA_PATH="${VPS_DATA_PATH:-/opt/botji/data/botji}"
SSH_KEY="${SSH_KEY:-}"
HERMES_UID="${HERMES_UID:-10000}"
HERMES_GID="${HERMES_GID:-$HERMES_UID}"

if [ -z "$VPS_HOST" ]; then
  echo "ERROR: VPS_HOST is required."
  echo ""
  echo "Usage:"
  echo "  VPS_HOST=<ip> ./scripts/codex-push-auth.sh"
  echo "  VPS_HOST=<ip> VPS_USER=root ./scripts/codex-push-auth.sh"
  exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=no"
if [ -n "$SSH_KEY" ]; then
  SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

echo "==> Local Codex home: $LOCAL_CODEX_HOME"

if [ ! -d "$LOCAL_CODEX_HOME" ]; then
  echo "ERROR: $LOCAL_CODEX_HOME does not exist."
  echo ""
  echo "Run 'codex login' first, then re-run this script."
  exit 1
fi

# Collect auth files — exclude config.toml (that is seeded by bootstrap)
AUTH_FILES=()
for candidate in \
  auth.json \
  .auth.json \
  credentials.json \
  .credentials.json \
  token.json \
  session.json \
  access_token.json \
; do
  f="$LOCAL_CODEX_HOME/$candidate"
  if [ -f "$f" ]; then
    AUTH_FILES+=("$f")
    echo "    found: $f"
  fi
done

if [ ${#AUTH_FILES[@]} -eq 0 ]; then
  echo ""
  echo "ERROR: No auth files found in $LOCAL_CODEX_HOME"
  echo ""
  echo "Possible reasons:"
  echo "  - 'codex login' was not run"
  echo "  - Codex stored auth in a different location"
  echo ""
  echo "Try setting LOCAL_CODEX_HOME to the directory where 'codex login' wrote files."
  echo "Example: LOCAL_CODEX_HOME=/tmp/codex-auth VPS_HOST=<ip> ./scripts/codex-push-auth.sh"
  exit 1
fi

REMOTE_CODEX_HOME="$VPS_DATA_PATH/.codex"

echo "==> Creating remote dir: $VPS_USER@$VPS_HOST:$REMOTE_CODEX_HOME"
# shellcheck disable=SC2029
ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" "mkdir -p $REMOTE_CODEX_HOME && chown $HERMES_UID:$HERMES_GID $REMOTE_CODEX_HOME 2>/dev/null || true && chmod 700 $REMOTE_CODEX_HOME"

echo "==> Copying auth files..."
for f in "${AUTH_FILES[@]}"; do
  fname="$(basename "$f")"
  scp $SSH_OPTS "$f" "$VPS_USER@$VPS_HOST:$REMOTE_CODEX_HOME/$fname"
  # shellcheck disable=SC2029
  ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" "chown $HERMES_UID:$HERMES_GID $REMOTE_CODEX_HOME/$fname 2>/dev/null || true; chmod 600 $REMOTE_CODEX_HOME/$fname"
  echo "    copied: $fname"
done

echo ""
echo "==> Done. Codex auth pushed to $VPS_USER@$VPS_HOST:$REMOTE_CODEX_HOME"
echo ""
echo "Verify with:"
echo "  ssh $VPS_USER@$VPS_HOST 'ls -la $REMOTE_CODEX_HOME'"
echo ""
echo "Test Codex from the container:"
echo "  ssh $VPS_USER@$VPS_HOST 'cd /opt/botji && docker compose exec hermes codex --version'"
