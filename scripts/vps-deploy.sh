#!/usr/bin/env bash
# Runs ON the VPS via SSH. CI vars come from /tmp/ci-vars.env (scp'd by Actions).
set -euo pipefail

ROLLBACK_ON_ERROR=0
ROLLBACK_DONE=0
cleanup() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ] && [ "${ROLLBACK_ON_ERROR:-0}" = "1" ] && [ "${ROLLBACK_DONE:-0}" != "1" ]; then
    rollback_to_previous "script_error" || true
  fi
  rm -f /tmp/ci-vars.env /tmp/vps-env.b64 /tmp/codex-auth.b64 /tmp/vps-deploy.sh
}
trap cleanup EXIT

if command -v flock >/dev/null 2>&1; then
  exec 9>/tmp/botji-deploy.lock
  if ! flock -w 600 9; then
    echo "ERROR: another deploy is still running" >&2
    exit 1
  fi
fi

# Load CI variables
source /tmp/ci-vars.env
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
git pull origin "$GIT_BRANCH"

echo "==> Write .env"
if [ -s /tmp/vps-env.b64 ]; then
  base64 -d /tmp/vps-env.b64 > .env
  chmod 600 .env
  echo "    .env written from CI secret"
fi
grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env 2>/dev/null \
  | grep -Ev '^(OPENROUTER_API_KEY|OPENAI_API_KEY)=' > .env.tmp || true
mv .env.tmp .env
tr -d '\r' < .env > .env.lf
mv .env.lf .env
chmod 600 .env
echo "    .env sanitized and legacy provider key entries removed"

set_env_var() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

if [ -n "${CI_TELEGRAM_BOT_TOKEN:-}" ]; then
  set_env_var TELEGRAM_BOT_TOKEN "$CI_TELEGRAM_BOT_TOKEN"
  echo "    TELEGRAM_BOT_TOKEN overridden from CI secret"
fi
if [ -n "${CI_TELEGRAM_BOT_USERNAME:-}" ]; then
  set_env_var TELEGRAM_BOT_USERNAME "$CI_TELEGRAM_BOT_USERNAME"
  echo "    TELEGRAM_BOT_USERNAME set to $CI_TELEGRAM_BOT_USERNAME"
fi

if grep -Eq '^BOTJI_DATA_DIR=/opt/data(/.*)?$' .env; then
  set_env_var BOTJI_DATA_DIR "./data/botji"
  echo "    BOTJI_DATA_DIR normalized to host tenant data path"
fi

# Apply model overrides from CI if provided; otherwise leave whatever is in .env.
# Model selection belongs in config.yaml — only override here when CI explicitly sets it.
if [ -n "${CI_CODEX_IMAGE_CHAT_MODEL:-}" ]; then
  set_env_var BOTJI_CODEX_IMAGE_CHAT_MODEL "$CI_CODEX_IMAGE_CHAT_MODEL"
  echo "    BOTJI_CODEX_IMAGE_CHAT_MODEL set from CI: $CI_CODEX_IMAGE_CHAT_MODEL"
fi
if [ -n "${CI_VISION_REVIEW_MODEL:-}" ]; then
  set_env_var BOTJI_VISION_REVIEW_MODEL "$CI_VISION_REVIEW_MODEL"
  echo "    BOTJI_VISION_REVIEW_MODEL set from CI: $CI_VISION_REVIEW_MODEL"
fi
if grep -Eq '^BOTJI_WORKSPACE_DIR=/workspace(/.*)?$' .env; then
  set_env_var BOTJI_WORKSPACE_DIR "./workspace"
  echo "    BOTJI_WORKSPACE_DIR normalized to host workspace path"
fi
chmod 600 .env

HERMES_RUNTIME_UID="$(grep -E '^HERMES_UID=' .env 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
HERMES_RUNTIME_GID="$(grep -E '^HERMES_GID=' .env 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
HERMES_RUNTIME_UID="${HERMES_RUNTIME_UID:-10000}"
HERMES_RUNTIME_GID="${HERMES_RUNTIME_GID:-$HERMES_RUNTIME_UID}"
DATA_DIR="$(grep -E '^BOTJI_DATA_DIR=' .env 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'" | tr -d '"')"
DATA_DIR="${DATA_DIR:-./data/botji}"
WORKSPACE_DIR="$(grep -E '^BOTJI_WORKSPACE_DIR=' .env 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'" | tr -d '"')"
WORKSPACE_DIR="${WORKSPACE_DIR:-./workspace}"
export HERMES_UID="$HERMES_RUNTIME_UID"
export HERMES_GID="$HERMES_RUNTIME_GID"
export BOTJI_DATA_DIR="$DATA_DIR"
export BOTJI_WORKSPACE_DIR="$WORKSPACE_DIR"

echo "==> Pull image: $IMAGE_REF"
docker pull "$IMAGE_REF"

echo "==> Force-update config.yaml (provider change requires overwrite)"
mkdir -p "$DATA_DIR"
cp seed/hermes/config.yaml "$DATA_DIR/config.yaml"
chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR" "$DATA_DIR/config.yaml" 2>/dev/null || true
echo "    config.yaml updated"

echo "==> Bootstrap seed (idempotent — skips existing files)"
export BOTJI_PROD_IMAGE="$IMAGE_REF"
# Ensure workspace is writable by the hermes user (UID 10000) before bootstrap runs
mkdir -p "$WORKSPACE_DIR"
chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$WORKSPACE_DIR" 2>/dev/null || true
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile bootstrap run --rm bootstrap

sync_code_components() {
  # Code artefacts — always reseed from the checked-out release. The glob-based
  # approach means new plugins/skills ship automatically without editing this script.
  mkdir -p "$DATA_DIR/skills" "$DATA_DIR/plugins" "$DATA_DIR/prompts" "$DATA_DIR/schemas"
  for skill_dir in seed/hermes/skills/botji-*; do
    [ -d "$skill_dir" ] || continue
    skill_name="$(basename "$skill_dir")"
    rm -rf "$DATA_DIR/skills/$skill_name"
    cp -R "$skill_dir" "$DATA_DIR/skills/" 2>/dev/null || true
  done
  for plugin_dir in seed/hermes/plugins/botji-*; do
    [ -d "$plugin_dir" ] || continue
    plugin_name="$(basename "$plugin_dir")"
    rm -rf "$DATA_DIR/plugins/$plugin_name"
    cp -R "$plugin_dir" "$DATA_DIR/plugins/"
    echo "    seeded plugin: $plugin_name"
  done
  rm -rf "$DATA_DIR/prompts"
  cp -R seed/hermes/prompts "$DATA_DIR/"
  cp seed/hermes/schemas/*.json "$DATA_DIR/schemas/" 2>/dev/null || true
  chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" \
    "$DATA_DIR/plugins" "$DATA_DIR/skills" "$DATA_DIR/prompts" "$DATA_DIR/schemas" 2>/dev/null || true
  mkdir -p "$DATA_DIR/verdicts"
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/verdicts" 2>/dev/null || true
}

rollback_to_previous() {
  ROLLBACK_DONE=1
  local failed_status="$1"
  echo "==> Rolling back after failed deploy ($failed_status)" >&2

  if [ -n "${PREVIOUS_GIT_HEAD:-}" ]; then
    git reset --hard "$PREVIOUS_GIT_HEAD" || true
    sync_code_components || true
    echo "    Repo and code components restored to $PREVIOUS_GIT_HEAD" >&2
  fi

  if [ -n "${PREVIOUS_IMAGE:-}" ]; then
    export BOTJI_PROD_IMAGE="$PREVIOUS_IMAGE"
    docker compose -f docker-compose.yml -f docker-compose.prod.yml \
      up -d --force-recreate --no-build --remove-orphans || true
    for _ in $(seq 1 15); do
      ROLLBACK_STATUS=$(docker inspect botji-hermes \
        --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")
      [ "$ROLLBACK_STATUS" = "healthy" ] && break
      sleep 4
    done
    echo "    Rollback status: ${ROLLBACK_STATUS:-unknown}" >&2
    [ "${ROLLBACK_STATUS:-}" = "healthy" ] || docker logs botji-hermes --tail 40 >&2 || true
  else
    echo "    No previous image available; manual intervention required" >&2
  fi
}

ROLLBACK_ON_ERROR=1

echo "==> Force-update code components (plugin, skills, schemas, prompts)"
sync_code_components
echo "    Plugins, skills, schemas, prompts updated from seed."

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

echo "==> Write Codex / Hermes auth"
if [ -s /tmp/codex-auth.b64 ]; then
  mkdir -p "$DATA_DIR/.codex"
  base64 -d /tmp/codex-auth.b64 > "$DATA_DIR/.codex/auth.json"
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/.codex" "$DATA_DIR/.codex/auth.json" 2>/dev/null || true
  chmod 600 "$DATA_DIR/.codex/auth.json"
  echo "    Codex auth.json written to $DATA_DIR/.codex/"

  # Hermes openai-codex does not read the raw Codex CLI auth shape directly.
  # Convert ~/.codex/auth.json tokens into the Hermes provider auth store.
  python3 - "$DATA_DIR/.codex/auth.json" "$DATA_DIR/auth.json" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

codex_auth_path = Path(sys.argv[1])
hermes_auth_path = Path(sys.argv[2])

codex_auth = json.loads(codex_auth_path.read_text())
tokens = codex_auth.get("tokens")
if not isinstance(tokens, dict):
    raise SystemExit("Codex auth.json is missing tokens")
for key in ("access_token", "refresh_token"):
    if not isinstance(tokens.get(key), str) or not tokens[key].strip():
        raise SystemExit(f"Codex auth.json is missing {key}")

try:
    hermes_auth = json.loads(hermes_auth_path.read_text()) if hermes_auth_path.exists() else {}
except Exception:
    hermes_auth = {}
if not isinstance(hermes_auth, dict):
    hermes_auth = {}

providers = hermes_auth.get("providers")
if not isinstance(providers, dict):
    providers = {}
    hermes_auth["providers"] = providers

state = providers.get("openai-codex")
if not isinstance(state, dict):
    state = {}
providers["openai-codex"] = state

state["tokens"] = tokens
state["last_refresh"] = (
    codex_auth.get("last_refresh")
    or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
)
state["auth_mode"] = "chatgpt"

hermes_auth_path.write_text(json.dumps(hermes_auth, indent=2, sort_keys=True) + "\n")
PY
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/auth.json" 2>/dev/null || true
  chmod 600 "$DATA_DIR/auth.json"
  echo "    Hermes openai-codex auth state written to $DATA_DIR/auth.json"
else
  echo "    CODEX_AUTH_B64 not set — skipping auth.json"
fi

echo "==> Clear Telegram webhook (ensures clean long-polling)"
BOT_TOKEN="$(grep -E '^TELEGRAM_BOT_TOKEN=' .env 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
if [ -n "$BOT_TOKEN" ]; then
  curl -s "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true" | grep -o '"ok":[^,}]*' || true
  echo "    Webhook cleared"
fi

echo "==> Start / reload"
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --force-recreate --no-build --remove-orphans

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
if [ "$STATUS" = "healthy" ]; then
  mkdir -p "$DATA_DIR/.botji"
  cat > "$DATA_DIR/.botji/LAST_DEPLOY.json" <<EOF
{
  "deployed_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "git_branch": "$GIT_BRANCH",
  "git_commit": "$(git rev-parse HEAD)",
  "image_ref": "$IMAGE_REF",
  "container_health": "$STATUS"
}
EOF
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/.botji/LAST_DEPLOY.json" 2>/dev/null || true
  ROLLBACK_ON_ERROR=0
  echo "==> Deploy complete."
else
  echo "ERROR: Container not healthy ($STATUS)" >&2
  docker logs botji-hermes --tail 40 >&2 || true
  rollback_to_previous "$STATUS"
  exit 1
fi

# Prune images not used by any running container. Prevents the ~30 GB disk
# accumulation seen after each CI deploy (old sha-* images pile up as <none>).
echo "==> Pruning unused images"
docker image prune -a -f 2>/dev/null || true

# Cleanup
rm -f /tmp/ci-vars.env /tmp/vps-env.b64 /tmp/codex-auth.b64 /tmp/vps-deploy.sh
