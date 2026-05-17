#!/usr/bin/env bash
# Runs ON the VPS via SSH. CI vars come from /tmp/ci-vars.env (scp'd by Actions).
set -euo pipefail

# Load CI variables
source /tmp/ci-vars.env
DEPLOY_PATH="${DEPLOY_PATH:-/opt/botji}"

echo "==> Deploy started on $(hostname) at $(date -u)"
echo "    Image: $IMAGE_REF"
echo "    Branch: $GIT_BRANCH"

cd "$DEPLOY_PATH"

echo "==> GHCR login"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

echo "==> Pull latest code"
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

echo "==> Force-update code components (plugin, skills, schemas, prompts)"
# These contain code, not user data — always reseed from the latest image.
for skill in botji-artifact-fidelity botji-2d-to-3d botji-source-fidelity botji-prompt-contract botji-codex-engineering; do
  rm -rf "$DATA_DIR/skills/$skill"
  cp -R "seed/hermes/skills/$skill" "$DATA_DIR/skills/" 2>/dev/null || true
done
rm -rf "$DATA_DIR/plugins/botji-artifacts"
cp -R seed/hermes/plugins/botji-artifacts "$DATA_DIR/plugins/"
rm -rf "$DATA_DIR/prompts"
cp -R seed/hermes/prompts "$DATA_DIR/"
cp seed/hermes/schemas/*.json "$DATA_DIR/schemas/" 2>/dev/null || true
chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" \
  "$DATA_DIR/plugins" "$DATA_DIR/skills" "$DATA_DIR/prompts" "$DATA_DIR/schemas" 2>/dev/null || true
echo "    Plugin, skills, schemas, prompts updated from seed."

echo "==> Write Codex / Hermes auth"
if [ -s /tmp/codex-auth.b64 ]; then
  mkdir -p "$DATA_DIR/.codex"
  base64 -d /tmp/codex-auth.b64 > "$DATA_DIR/.codex/auth.json"
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/.codex" "$DATA_DIR/.codex/auth.json" 2>/dev/null || true
  chmod 600 "$DATA_DIR/.codex/auth.json"
  echo "    Codex auth.json written to $DATA_DIR/.codex/"
  # Hermes looks for auth.json at $HERMES_HOME/auth.json (= /opt/data/auth.json inside container)
  base64 -d /tmp/codex-auth.b64 > "$DATA_DIR/auth.json"
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/auth.json" 2>/dev/null || true
  chmod 600 "$DATA_DIR/auth.json"
  echo "    Hermes auth.json written to $DATA_DIR/auth.json"
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
for i in $(seq 1 15); do
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
  echo "==> Deploy complete."
else
  echo "ERROR: Container not healthy ($STATUS)" >&2
  docker logs botji-hermes --tail 20 >&2 || true
  exit 1
fi

# Cleanup
rm -f /tmp/ci-vars.env /tmp/vps-env.b64 /tmp/codex-auth.b64 /tmp/vps-deploy.sh
