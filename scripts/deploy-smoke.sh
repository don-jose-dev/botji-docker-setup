#!/usr/bin/env bash
# deploy-smoke.sh — pre-flight plugin smoke + post-deploy health waiting /
# smoke check. Sourced by deploy.sh. Defines:
#   preflight_plugin_smoke  — runs scripts/smoke-plugin.sh against the
#                             primary tenant's plugins (aborts deploy on fail).
#   clear_telegram_webhook_from_env ENV_FILE
#                           — best-effort Telegram deleteWebhook so polling
#                             starts clean without dropping pending updates.
#   clear_telegram_webhook  — primary-tenant wrapper around .env.
#   wait_for_primary_health — polls botji-hermes for up to 60s until healthy;
#                             echoes status into the caller via STATUS global.
#   run_primary_smoke       — calls scripts/vps-postdeploy-smoke.sh on the
#                             primary tenant (assumes container is healthy).
#
# Reads from setup_env: DATA_DIR. Reads from main: STATUS (set by
# wait_for_primary_health for the orchestrator to branch on).

preflight_plugin_smoke() {
  echo "==> Pre-flight plugin smoke test"
  # Imports every plugin's submodules + asserts public symbols exist. Aborts the
  # deploy before container start if any plugin is broken — prevents silent outages
  # where bad imports ship to prod and the agent bypasses the fidelity harness.
  if [ -x scripts/smoke-plugin.sh ]; then
    if ! PLUGINS_ROOT="$DATA_DIR/plugins" bash scripts/smoke-plugin.sh; then
      echo "ERROR: Plugin smoke test FAILED — aborting deploy before container start." >&2
      echo "       Run locally: PLUGINS_ROOT=seed/hermes/plugins bash scripts/smoke-plugin.sh" >&2
      exit 2
    fi
  else
    echo "    WARNING: scripts/smoke-plugin.sh missing — skipping pre-flight smoke."
  fi
}

clear_telegram_webhook_from_env() {
  local env_file="${1:-.env}"
  echo "==> Clear Telegram webhook for $env_file (preserve pending updates)"
  BOT_TOKEN="$(grep -E '^TELEGRAM_BOT_TOKEN=' "$env_file" 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
  if [ -n "$BOT_TOKEN" ]; then
    curl -s "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=false" | grep -o '"ok":[^,}]*' || true
    echo "    Webhook cleared"
  fi
}

clear_telegram_webhook() {
  clear_telegram_webhook_from_env .env
}

wait_for_primary_health() {
  echo "==> Waiting for healthy..."
  for _ in $(seq 1 15); do
    STATUS=$(docker inspect botji-hermes \
      --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")
    [ "$STATUS" = "healthy" ] && break
    sleep 4
  done

  echo "==> Smoke check"
  sleep 10
  STATUS=$(docker inspect botji-hermes \
    --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")
  echo "    Status: $STATUS"
}

run_primary_smoke() {
  if [ -f scripts/vps-postdeploy-smoke.sh ]; then
    echo "==> Post-deploy smoke"
    BOTJI_CONTAINER_NAME="botji-hermes" \
      BOTJI_TENANT_ID="${BOTJI_TENANT_ID:-botji}" \
      bash scripts/vps-postdeploy-smoke.sh
  else
    echo "    WARNING: scripts/vps-postdeploy-smoke.sh missing - skipping post-deploy smoke."
  fi
}
