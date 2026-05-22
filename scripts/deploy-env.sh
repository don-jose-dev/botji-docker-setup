#!/usr/bin/env bash
# deploy-env.sh — .env sanitize, CI overrides, BOTJI_DATA_DIR normalization.
#
# Sourced by deploy.sh. Defines:
#   set_env_var KEY VALUE  — idempotently set KEY=VALUE in .env
#   setup_env              — write/sanitize .env, apply CI model + Telegram
#                            overrides, normalize BOTJI_DATA_DIR /
#                            BOTJI_WORKSPACE_DIR, and EXPORT the resolved
#                            HERMES_UID / HERMES_GID / BOTJI_DATA_DIR /
#                            BOTJI_WORKSPACE_DIR (also assigned to the
#                            HERMES_RUNTIME_UID/_GID, DATA_DIR, WORKSPACE_DIR
#                            shell globals used by later modules).
#
# Reads from CI: CI_TELEGRAM_BOT_TOKEN, CI_TELEGRAM_BOT_USERNAME,
#                CI_IMAGE_MODEL, CI_CODEX_IMAGE_CHAT_MODEL,
#                CI_VISION_REVIEW_MODEL.

set_env_var() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

setup_env() {
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

  # Normalize model env overrides. VPS_ENV is long-lived and has previously kept
  # BOTJI_* model overrides pinned to gpt-5.5 after config.yaml was moved back to
  # gpt-5.4-mini. That silently puts image turns on the slower path. Use fast,
  # reviewed defaults unless CI deliberately supplies a replacement.
  BOTJI_DEPLOY_IMAGE_MODEL="${CI_IMAGE_MODEL:-gpt-image-2}"
  BOTJI_DEPLOY_CODEX_IMAGE_CHAT_MODEL="${CI_CODEX_IMAGE_CHAT_MODEL:-gpt-5.4-mini}"
  BOTJI_DEPLOY_VISION_REVIEW_MODEL="${CI_VISION_REVIEW_MODEL:-gpt-5.4-mini}"
  set_env_var BOTJI_IMAGE_MODEL "$BOTJI_DEPLOY_IMAGE_MODEL"
  set_env_var BOTJI_CODEX_IMAGE_CHAT_MODEL "$BOTJI_DEPLOY_CODEX_IMAGE_CHAT_MODEL"
  set_env_var BOTJI_VISION_REVIEW_MODEL "$BOTJI_DEPLOY_VISION_REVIEW_MODEL"
  echo "    Botji model env normalized: image=$BOTJI_DEPLOY_IMAGE_MODEL image_chat=$BOTJI_DEPLOY_CODEX_IMAGE_CHAT_MODEL vision_review=$BOTJI_DEPLOY_VISION_REVIEW_MODEL"
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
}
