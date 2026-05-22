#!/usr/bin/env bash
# Runs ON the VPS via SSH. CI vars come from /tmp/ci-vars.env (scp'd by Actions).
#
# Thin wrapper that owns the trap, single-deploy lock, and CI var loading,
# then delegates the actual flow to scripts/deploy.sh which sources the
# focused deploy-*.sh modules. The deploy workflow scp's all
# scripts/deploy*.sh files alongside this one into /tmp/.
set -euo pipefail

ROLLBACK_ON_ERROR=0
ROLLBACK_DONE=0
cleanup() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ] && [ "${ROLLBACK_ON_ERROR:-0}" = "1" ] && [ "${ROLLBACK_DONE:-0}" != "1" ]; then
    rollback_to_previous "script_error" || true
  fi
  rm -f /tmp/ci-vars.env /tmp/vps-env.b64 /tmp/codex-auth.b64 \
        /tmp/vps-deploy.sh /tmp/deploy.sh /tmp/deploy-env.sh \
        /tmp/deploy-config.sh /tmp/deploy-tenants.sh /tmp/deploy-smoke.sh
}
trap cleanup EXIT

if command -v flock >/dev/null 2>&1; then
  exec 9>/tmp/botji-deploy.lock
  if ! flock -w 600 9; then
    echo "ERROR: another deploy is still running" >&2
    exit 1
  fi
fi

# Load CI variables before sourcing the orchestrator so deploy-env.sh sees
# CI_TELEGRAM_BOT_TOKEN, CI_IMAGE_MODEL, etc.
# shellcheck disable=SC1091
source /tmp/ci-vars.env

# Resolve sibling module dir. The deploy workflow ships every scripts/deploy*.sh
# file into the same directory (typically /tmp/) as this wrapper.
VPS_DEPLOY_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy.sh
source "$VPS_DEPLOY_SCRIPT_DIR/deploy.sh"
