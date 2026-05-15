#!/usr/bin/env bash
# Upload latest local snapshot to cloud storage via rclone.
# Requires rclone configured: rclone config
# Supports: Backblaze B2, S3, GCS, DigitalOcean Spaces, etc.
set -euo pipefail

REMOTE="${RCLONE_REMOTE:-b2:botji-backups}"        # rclone remote:bucket
BACKUP_DIR="${BACKUP_DIR:-./backups}"
TENANT="${BOTJI_TENANT_ID:-botji}"
KEEP_REMOTE="${KEEP_REMOTE:-14}"                    # days to keep remote backups

# Create a fresh snapshot first
./scripts/snapshot.sh

# Find the latest snapshot
LATEST=$(ls -t "$BACKUP_DIR"/${TENANT}-hermes-home-*.tgz 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
    echo "ERROR: No snapshots found in $BACKUP_DIR"
    exit 1
fi

echo "==> Uploading $LATEST to $REMOTE..."
rclone copy "$LATEST" "$REMOTE/" --progress

echo "==> Pruning remote backups older than $KEEP_REMOTE days..."
rclone delete "$REMOTE/" \
    --min-age "${KEEP_REMOTE}d" \
    --include "*.tgz" \
    --verbose 2>/dev/null || true

echo "==> Done. Remote contents:"
rclone ls "$REMOTE/" 2>/dev/null | tail -10
