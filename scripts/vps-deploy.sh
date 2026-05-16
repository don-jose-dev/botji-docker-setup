#!/usr/bin/env bash
# Runs ON the VPS via SSH. CI vars come from /tmp/ci-vars.env (scp'd by Actions).
set -euo pipefail

# Load CI variables
source /tmp/ci-vars.env
DEPLOY_PATH="${DEPLOY_PATH:-/opt/botji}"

echo "==> Deploy started on $(hostname) at $(date -u)"
echo "    Image: $IMAGE_TAG"
echo "    Branch: $GIT_BRANCH"

cd "$DEPLOY_PATH"

echo "==> GHCR login"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --stdin

echo "==> Pull latest code"
git pull origin "$GIT_BRANCH"

echo "==> Write .env"
if [ -s /tmp/vps-env.b64 ]; then
  base64 -d /tmp/vps-env.b64 > .env
  chmod 600 .env
  echo "    .env written from CI secret"
fi

echo "==> Pull image: $IMAGE_TAG"
BOTJI_PROD_IMAGE="$IMAGE_TAG" \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml pull

echo "==> Reload"
BOTJI_PROD_IMAGE="$IMAGE_TAG" make reload

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
rm -f /tmp/ci-vars.env /tmp/vps-env.b64 /tmp/vps-deploy.sh
