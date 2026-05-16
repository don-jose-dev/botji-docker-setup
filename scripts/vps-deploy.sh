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
grep -Ev '^(OPENROUTER_API_KEY|OPENAI_API_KEY)=' .env > .env.tmp 2>/dev/null || true
mv .env.tmp .env
chmod 600 .env
echo "    legacy provider key entries removed from .env"

HERMES_RUNTIME_UID="$(grep -E '^HERMES_UID=' .env 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
HERMES_RUNTIME_GID="$(grep -E '^HERMES_GID=' .env 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
HERMES_RUNTIME_UID="${HERMES_RUNTIME_UID:-10000}"
HERMES_RUNTIME_GID="${HERMES_RUNTIME_GID:-$HERMES_RUNTIME_UID}"

echo "==> Pull image: $IMAGE_REF"
docker pull "$IMAGE_REF"

echo "==> Force-update config.yaml (provider change requires overwrite)"
DATA_DIR_CFG="$(grep -E '^BOTJI_DATA_DIR=' .env 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
DATA_DIR_CFG="${DATA_DIR_CFG:-./data/botji}"
cp seed/hermes/config.yaml "$DATA_DIR_CFG/config.yaml" 2>/dev/null && echo "    config.yaml updated" || echo "    config.yaml update skipped"

echo "==> Bootstrap seed (idempotent — skips existing files)"
export BOTJI_PROD_IMAGE="$IMAGE_REF"
# Ensure workspace is writable by the hermes user (UID 10000) before bootstrap runs
WORKSPACE_DIR="$(grep -E '^BOTJI_WORKSPACE_DIR=' .env 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
WORKSPACE_DIR="${WORKSPACE_DIR:-./workspace}"
mkdir -p "$WORKSPACE_DIR"
chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$WORKSPACE_DIR" 2>/dev/null || true
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile bootstrap run --rm bootstrap

echo "==> Write Codex auth"
DATA_DIR="$(grep -E '^BOTJI_DATA_DIR=' .env 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
DATA_DIR="${DATA_DIR:-./data/botji}"
if [ -s /tmp/codex-auth.b64 ]; then
  mkdir -p "$DATA_DIR/.codex"
  base64 -d /tmp/codex-auth.b64 > "$DATA_DIR/.codex/auth.json"
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/.codex" "$DATA_DIR/.codex/auth.json" 2>/dev/null || true
  chmod 600 "$DATA_DIR/.codex/auth.json"
  echo "    Codex auth.json written to $DATA_DIR/.codex/"
else
  echo "    CODEX_AUTH_B64 not set — skipping auth.json"
fi

echo "==> Start / reload"
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --no-build --remove-orphans

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
