#!/usr/bin/env bash
# Validate that two or more Botji tenant env files are isolated and model-aligned.
set -euo pipefail

require_secrets=0
prod_compose=0
allow_model_drift=0
skip_compose_config=0
env_files=()

usage() {
  cat <<'EOF'
Usage: bash scripts/smoke-tenants.sh [--require-secrets] [--prod] [--allow-model-drift] [--skip-compose-config] ENV_FILE...

Checks:
  - unique BOTJI_TENANT_ID, BOTJI_DATA_DIR, BOTJI_WORKSPACE_DIR, dashboard/API ports
  - unique Telegram bot tokens when present
  - same BOTJI_CODEX_IMAGE_CHAT_MODEL and BOTJI_VISION_REVIEW_MODEL across tenants
  - docker compose config renders with each env file and project name
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --require-secrets) require_secrets=1 ;;
    --prod) prod_compose=1 ;;
    --allow-model-drift) allow_model_drift=1 ;;
    --skip-compose-config) skip_compose_config=1 ;;
    -h|--help) usage; exit 0 ;;
    --*) echo "ERROR: unknown option $1" >&2; usage >&2; exit 2 ;;
    *) env_files+=("$1") ;;
  esac
  shift
done

if [ "${#env_files[@]}" -lt 2 ]; then
  echo "ERROR: provide at least two tenant env files" >&2
  usage >&2
  exit 2
fi

project_re='^[a-z0-9][a-z0-9_-]*$'
failures=()
tenants=()
data_dirs=()
workspace_dirs=()
api_ports=()
dashboard_ports=()
token_hashes=()
chat_models=()
vision_models=()

sha_value() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | awk '{print $1}'
  else
    printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
  fi
}

load_env() {
  local env_file="$1"
  set -a
  # shellcheck disable=SC1090
  . <(tr -d '\r' < "$env_file")
  set +a
}

contains_duplicate() {
  local value="$1"; shift
  local item
  for item in "$@"; do
    [ "$item" = "$value" ] && return 0
  done
  return 1
}

while IFS='|' read -r kind env_file a b c d e f g h; do
  if [ "$kind" = "FAIL" ]; then
    failures+=("$env_file: $a")
    continue
  fi
  if [ "$kind" != "DATA" ]; then
    continue
  fi
  tenant="$a"; data_dir="$b"; workspace_dir="$c"; api_port="$d"
  dashboard_port="$e"; token_hash="$f"; chat_model="$g"; vision_model="$h"

  contains_duplicate "$tenant" "${tenants[@]}" && failures+=("$env_file: duplicate BOTJI_TENANT_ID '$tenant'")
  contains_duplicate "$data_dir" "${data_dirs[@]}" && failures+=("$env_file: duplicate BOTJI_DATA_DIR '$data_dir'")
  contains_duplicate "$workspace_dir" "${workspace_dirs[@]}" && failures+=("$env_file: duplicate BOTJI_WORKSPACE_DIR '$workspace_dir'")
  contains_duplicate "$api_port" "${api_ports[@]}" && failures+=("$env_file: duplicate HERMES_API_PORT '$api_port'")
  contains_duplicate "$dashboard_port" "${dashboard_ports[@]}" && failures+=("$env_file: duplicate HERMES_DASHBOARD_PORT '$dashboard_port'")
  if [ -n "$token_hash" ] && contains_duplicate "$token_hash" "${token_hashes[@]}"; then
    failures+=("$env_file: duplicate TELEGRAM_BOT_TOKEN")
  fi
  if [ "$allow_model_drift" = "0" ] && [ "${#chat_models[@]}" -gt 0 ] && [ "$chat_model" != "${chat_models[0]}" ]; then
    failures+=("$env_file: BOTJI_CODEX_IMAGE_CHAT_MODEL drift '$chat_model' != '${chat_models[0]}'")
  fi
  if [ "$allow_model_drift" = "0" ] && [ "${#vision_models[@]}" -gt 0 ] && [ "$vision_model" != "${vision_models[0]}" ]; then
    failures+=("$env_file: BOTJI_VISION_REVIEW_MODEL drift '$vision_model' != '${vision_models[0]}'")
  fi

  tenants+=("$tenant")
  data_dirs+=("$data_dir")
  workspace_dirs+=("$workspace_dir")
  api_ports+=("$api_port")
  dashboard_ports+=("$dashboard_port")
  [ -n "$token_hash" ] && token_hashes+=("$token_hash")
  chat_models+=("$chat_model")
  vision_models+=("$vision_model")
done < <(
  for env_file in "${env_files[@]}"; do
    if [ ! -f "$env_file" ]; then
      echo "FAIL|$env_file|file not found"
      continue
    fi

    (
    load_env "$env_file"
    tenant="${BOTJI_TENANT_ID:-}"
    data_dir="${BOTJI_DATA_DIR:-}"
    workspace_dir="${BOTJI_WORKSPACE_DIR:-}"
    api_port="${HERMES_API_PORT:-8642}"
    dashboard_port="${HERMES_DASHBOARD_PORT:-9119}"
    token="${TELEGRAM_BOT_TOKEN:-}"
    chat_model="${BOTJI_CODEX_IMAGE_CHAT_MODEL:-gpt-5.4-mini}"
    vision_model="${BOTJI_VISION_REVIEW_MODEL:-gpt-5.4-mini}"

    [ -n "$tenant" ] || { echo "FAIL|$env_file|BOTJI_TENANT_ID is required"; exit 0; }
    if ! printf '%s' "$tenant" | grep -Eq "$project_re"; then
      echo "FAIL|$env_file|BOTJI_TENANT_ID '$tenant' is not a valid Compose project name"
    fi
    [ -n "$data_dir" ] || echo "FAIL|$env_file|BOTJI_DATA_DIR is required"
    [ -n "$workspace_dir" ] || echo "FAIL|$env_file|BOTJI_WORKSPACE_DIR is required"
    if [ "$require_secrets" = "1" ] && [ -z "$token" ]; then
      echo "FAIL|$env_file|TELEGRAM_BOT_TOKEN is required"
    fi

    if [ "$skip_compose_config" = "0" ]; then
      compose_args=(docker compose --env-file "$env_file" -p "$tenant" -f docker-compose.yml)
      if [ "$prod_compose" = "1" ]; then
        compose_args+=(-f docker-compose.prod.yml)
      fi
      if ! "${compose_args[@]}" config --quiet >/dev/null 2>&1; then
        echo "FAIL|$env_file|docker compose config failed"
      fi
    fi

    token_hash=""
    if [ -n "$token" ]; then
      token_hash="$(sha_value "$token")"
    fi
    printf 'DATA|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
      "$env_file" "$tenant" "$data_dir" "$workspace_dir" "$api_port" \
      "$dashboard_port" "$token_hash" "$chat_model" "$vision_model"
    )
  done
)

if [ "${#failures[@]}" -gt 0 ]; then
  printf 'Tenant smoke failed:\n' >&2
  printf '  - %s\n' "${failures[@]}" >&2
  exit 2
fi

if [ "${#tenants[@]}" -ne "${#env_files[@]}" ]; then
  printf 'Tenant smoke failed:\n' >&2
  printf '  - expected %d tenant records, got %d\n' "${#env_files[@]}" "${#tenants[@]}" >&2
  exit 2
fi

printf 'Tenant smoke passed for %d tenant env file(s): %s\n' "${#tenants[@]}" "${tenants[*]}"
