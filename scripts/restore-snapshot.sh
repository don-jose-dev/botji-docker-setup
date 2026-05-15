#!/usr/bin/env bash
# Restore a Botji snapshot created by snapshot.sh
set -euo pipefail

BACKUP_FILE="${1:-}"
DATA_DIR="${BOTJI_DATA_DIR:-./data/botji}"
TENANT="${BOTJI_TENANT_ID:-botji}"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: $0 <backup-file.tgz>"
    echo ""
    echo "Available backups:"
    ls -lh backups/*.tgz 2>/dev/null || echo "  (none in ./backups/)"
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "==> Stopping container..."
docker compose stop hermes 2>/dev/null || true

echo "==> Backing up current data (safety copy)..."
SAFETY="backups/${TENANT}-pre-restore-$(date -u +"%Y%m%dT%H%M%SZ").tgz"
tar -czf "$SAFETY" -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")" 2>/dev/null || true
echo "    Safety copy: $SAFETY"

echo "==> Restoring from $BACKUP_FILE..."
mkdir -p "$(dirname "$DATA_DIR")"
tar -xzf "$BACKUP_FILE" -C "$(dirname "$DATA_DIR")"
echo "    Restored to: $DATA_DIR"

echo "==> Restarting container..."
docker compose start hermes

echo ""
echo "==> Restore complete. Monitor with: make logs"
