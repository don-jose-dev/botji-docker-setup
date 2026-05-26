#!/usr/bin/env bash
# deploy.sh — orchestrator for the VPS deploy flow. Sourced by
# scripts/vps-deploy.sh after the trap, lock, and /tmp/ci-vars.env have been
# loaded. Sources the focused deploy-*.sh modules and calls their functions
# in the order the original monolithic vps-deploy.sh did.
#
# Behavior MUST stay identical to pre-split vps-deploy.sh: same env contract,
# same exit codes, same trap-driven rollback. Functions live in modules; the
# control flow lives here.

set -euo pipefail

# Resolve sibling-module directory whether we were invoked from the repo or
# from /tmp/ (the deploy workflow scp's the deploy*.sh files to /tmp/).
DEPLOY_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy-env.sh
source "$DEPLOY_SCRIPT_DIR/deploy-env.sh"
# shellcheck source=scripts/deploy-config.sh
source "$DEPLOY_SCRIPT_DIR/deploy-config.sh"
# shellcheck source=scripts/deploy-tenants.sh
source "$DEPLOY_SCRIPT_DIR/deploy-tenants.sh"
# shellcheck source=scripts/deploy-smoke.sh
source "$DEPLOY_SCRIPT_DIR/deploy-smoke.sh"

DEPLOY_PATH="${DEPLOY_PATH:-/opt/botji}"

echo "==> Deploy started on $(hostname) at $(date -u)"
echo "    Image: $IMAGE_REF"
echo "    Branch: $GIT_BRANCH"

cd "$DEPLOY_PATH"

PREVIOUS_GIT_HEAD="$(git rev-parse HEAD 2>/dev/null || true)"
PREVIOUS_IMAGE="$(docker inspect botji-hermes --format='{{.Config.Image}}' 2>/dev/null || true)"
echo "    Previous commit: ${PREVIOUS_GIT_HEAD:-none}"
echo "    Previous image: ${PREVIOUS_IMAGE:-none}"

echo "==> GHCR login"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

echo "==> Pull latest code"
git reset --hard HEAD
# The VPS checkout can contain previously scp'd/generated release files that
# later become tracked by the repo. Clean only repo-owned release trees before
# pulling so git can fast-forward without touching tenant data, .env, or logs.
git clean -fd -- \
  runtime/bin \
  seed/hermes/plugins \
  seed/hermes/skills \
  scripts/vps-postdeploy-smoke.sh
git pull --ff-only origin "$GIT_BRANCH"

setup_env

echo "==> Pull image: $IMAGE_REF"
docker pull "$IMAGE_REF"

install_config
migrate_kanban_db
bootstrap_workspace

# shellcheck disable=SC2034  # read by the cleanup trap in vps-deploy.sh
ROLLBACK_ON_ERROR=1

echo "==> Force-update code components (plugin, skills, schemas, prompts)"
sync_code_components
echo "    Plugins, skills, schemas, prompts updated from seed."

preflight_plugin_smoke
write_codex_auth
clear_telegram_webhook
start_primary_tenant
wait_for_primary_health

if [ "$STATUS" = "healthy" ]; then
  # Upstream Hermes startup can touch /opt/data/auth.json before the gateway
  # runs as the hermes UID. Re-assert ownership after the container is healthy
  # so the first real Telegram turn can read the provider auth store.
  ensure_auth_file_owner "$DATA_DIR" "$HERMES_RUNTIME_UID" "$HERMES_RUNTIME_GID" "post-start Hermes"
  run_primary_smoke
  record_last_deploy "$STATUS"
  # shellcheck disable=SC2034  # read by the cleanup trap in vps-deploy.sh
  ROLLBACK_ON_ERROR=0
  echo "==> Primary deploy complete."
else
  echo "ERROR: Container not healthy ($STATUS)" >&2
  docker logs botji-hermes --tail 40 >&2 || true
  rollback_to_previous "$STATUS"
  exit 1
fi

run_additional_tenants
echo "==> Deploy complete."

# Prune images not used by any running container. Prevents the ~30 GB disk
# accumulation seen after each CI deploy (old sha-* images pile up as <none>).
echo "==> Pruning unused images"
docker image prune -a -f 2>/dev/null || true

# Cleanup — also handled by the EXIT trap in vps-deploy.sh, but the original
# monolithic script removed these inline at the very end too. Keep for parity.
rm -f /tmp/ci-vars.env /tmp/vps-env.b64 /tmp/codex-auth*.b64 \
      /tmp/vps-deploy.sh /tmp/deploy.sh /tmp/deploy-env.sh \
      /tmp/deploy-config.sh /tmp/deploy-tenants.sh /tmp/deploy-smoke.sh
