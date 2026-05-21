#!/usr/bin/env bash
# Runs ON the VPS after deploy. Verifies tenant runtime, gates, and recent budgets.
set -euo pipefail

TENANT_ID="${BOTJI_TENANT_ID:-botji}"
CONTAINER_NAME="${BOTJI_CONTAINER_NAME:-${TENANT_ID}-hermes}"
EXPECTED_MODEL="${BOTJI_EXPECTED_MODEL:-gpt-5.4-mini}"
EXPECTED_PROVIDER="${BOTJI_EXPECTED_PROVIDER:-openai-codex}"
EXPECTED_IMAGE_INPUT_MODE="${BOTJI_EXPECTED_IMAGE_INPUT_MODE:-native}"
EXPECTED_IMAGE_MODEL="${BOTJI_EXPECTED_IMAGE_MODEL:-gpt-image-2}"
EXPECTED_IMAGE_CHAT_MODEL="${BOTJI_EXPECTED_CODEX_IMAGE_CHAT_MODEL:-gpt-5.4-mini}"
EXPECTED_VISION_REVIEW_MODEL="${BOTJI_EXPECTED_VISION_REVIEW_MODEL:-gpt-5.4-mini}"
SINCE_MINUTES="${BOTJI_BUDGET_SINCE_MINUTES:-30}"
MAX_RESPONSE_SECONDS="${BOTJI_MAX_RESPONSE_SECONDS:-180}"
MAX_API_CALLS="${BOTJI_MAX_API_CALLS:-8}"
MAX_SESSION_API_CALLS="${BOTJI_MAX_SESSION_API_CALLS:-14}"
MAX_SESSION_TOOL_TURNS="${BOTJI_MAX_SESSION_TOOL_TURNS:-14}"

echo "=== postdeploy: container health ==="
docker inspect "$CONTAINER_NAME" --format='{{.Name}} started={{.State.StartedAt}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'

echo "=== postdeploy: runtime tenant harness ==="
docker exec "$CONTAINER_NAME" botji-runtime-harness \
  --expected-tenant "$TENANT_ID" \
  --expected-model "$EXPECTED_MODEL" \
  --expected-provider "$EXPECTED_PROVIDER" \
  --expected-image-input-mode "$EXPECTED_IMAGE_INPUT_MODE" \
  --expected-image-model "$EXPECTED_IMAGE_MODEL" \
  --expected-image-chat-model "$EXPECTED_IMAGE_CHAT_MODEL" \
  --expected-vision-review-model "$EXPECTED_VISION_REVIEW_MODEL" \
  --require-token \
  --require-allowlist

echo "=== postdeploy: allowlist harness ==="
docker exec "$CONTAINER_NAME" botji-allowlist-harness

echo "=== postdeploy: gate harness ==="
docker exec "$CONTAINER_NAME" botji-gate-harness

echo "=== postdeploy: core substrate harness ==="
docker exec "$CONTAINER_NAME" botji-core-harness

echo "=== postdeploy: fidelity guard harness ==="
docker exec "$CONTAINER_NAME" botji-fidelity-guard-harness

echo "=== postdeploy: deterministic artifact mini-harness ==="
docker exec "$CONTAINER_NAME" botji-artifact-harness \
  --adapters text,image \
  --strict \
  --require-modality-comparators >/tmp/botji-artifact-mini-harness.json

echo "=== postdeploy: log budget ==="
docker exec "$CONTAINER_NAME" botji-log-budget \
  --since-minutes "$SINCE_MINUTES" \
  --allow-empty \
  --max-response-seconds "$MAX_RESPONSE_SECONDS" \
  --max-api-calls "$MAX_API_CALLS" \
  --max-session-api-calls "$MAX_SESSION_API_CALLS" \
  --max-session-tool-turns "$MAX_SESSION_TOOL_TURNS" \
  --max-tool-count artifact_transform=1 \
  --max-tool-count artifact_review=1 \
  --max-tool-seconds artifact_transform=90 \
  --max-tool-seconds artifact_extract_manifest=90

if [ "${RUN_LIVE_PROVIDER_E2E:-0}" = "1" ]; then
  echo "=== postdeploy: live provider e2e ==="
  docker exec "$CONTAINER_NAME" botji-artifact-e2e \
    --quality low \
    --max-attempts "${BOTJI_LIVE_E2E_MAX_ATTEMPTS:-1}" \
    --require-pass
else
  echo "=== postdeploy: live provider e2e skipped (RUN_LIVE_PROVIDER_E2E=0) ==="
fi
