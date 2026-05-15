#!/usr/bin/env bash
set -euo pipefail

source .env 2>/dev/null || true
DATA_DIR="${BOTJI_DATA_DIR:-./data/botji}"
TENANT="${BOTJI_TENANT_ID:-botji}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p backups

tar --exclude='*.log' \
    --exclude='logs/*' \
    -czf "backups/${TENANT}-hermes-home-${STAMP}.tgz" \
    -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"

echo "Snapshot written: backups/${TENANT}-hermes-home-${STAMP}.tgz"
