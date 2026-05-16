#!/usr/bin/env bash
# Runs ON the VPS via SSH. Called by GitHub Actions deploy job.
# All env vars are passed from the Actions runner via SSH.
set -euo pipefail

echo "==> Deploy started on $(hostname) at $(date -u)"

cd "${DEPLOY_PATH:-/opt/botji}"

echo "==> GHCR login"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --stdin

echo "==> Pull latest code"
git pull origin "${GIT_BRANCH:-master}"

echo "==> Write .env"
if [ -n "${VPS_ENV_B64:-}" ]; then
  printf '%s' "$VPS_ENV_B64" | base64 -d > .env
  chmod 600 .env
  echo "    .env written from secret"
fi

echo "==> Pull image: ${IMAGE_TAG}"
BOTJI_PROD_IMAGE="${IMAGE_TAG}" \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml pull

echo "==> Reload"
BOTJI_PROD_IMAGE="${IMAGE_TAG}" make reload

echo "==> Smoke check"
sleep 10
STATUS=$(docker inspect botji-hermes \
  --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")
echo "    Status: $STATUS"
if [ "$STATUS" = "healthy" ]; then
  echo "Deploy complete."
else
  echo "ERROR: Container not healthy ($STATUS). Check: docker logs botji-hermes" >&2
  exit 1
fi
