#!/usr/bin/env bash
# Runs ON the VPS. Archives active Hermes session files so new turns reload
# current SOUL/skills after a deploy.
set -euo pipefail

if [ "$#" -gt 0 ]; then
  BASES=("$@")
else
  BASES=(/opt/botji /opt/botji-degain)
fi
STAMP="${BOTJI_SESSION_ROTATE_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"

get_env_var() {
  local file="$1" key="$2" default="$3" value
  value="$(grep -E "^${key}=" "$file" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d "'" | tr -d '"')"
  printf '%s' "${value:-$default}"
}

for base in "${BASES[@]}"; do
  [ -d "$base" ] || continue
  env_file="$base/.env"
  tenant="$(get_env_var "$env_file" BOTJI_TENANT_ID "$(basename "$base")")"
  data_dir="$(get_env_var "$env_file" BOTJI_DATA_DIR "./data/$tenant")"
  case "$data_dir" in
    /*) sessions_dir="$data_dir/sessions" ;;
    *) sessions_dir="$base/$data_dir/sessions" ;;
  esac
  archive_dir="$sessions_dir/archive/$STAMP"
  mkdir -p "$archive_dir"
  moved=0
  shopt -s nullglob
  for file in "$sessions_dir"/*.jsonl "$sessions_dir"/session_*.json "$sessions_dir"/sessions.json; do
    [ -f "$file" ] || continue
    mv "$file" "$archive_dir/"
    moved=$((moved + 1))
  done
  shopt -u nullglob
  echo "$tenant: archived $moved session file(s) to $archive_dir"
done
