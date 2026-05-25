#!/usr/bin/env bash
# deploy-config.sh — config.yaml install, kanban DB schema migration, codex
# auth conversion. Sourced by deploy.sh. Defines:
#   install_config       — install seed/hermes/config.yaml into $DATA_DIR with
#                          correct mode/owner (UID 10000 must be able to read).
#   migrate_kanban_db    — best-effort ADD COLUMN session_id to kanban tables
#                          on the primary tenant's kanban.db.
#   migrate_kanban_db_for_tenant PATH UID GID
#                        — same migration against an additional tenant's
#                          kanban.db (kept distinct from the primary variant
#                          to preserve the original log message format).
#   write_codex_auth     — decode /tmp/codex-auth.b64 into $DATA_DIR/.codex/
#                          and convert into the Hermes provider auth store at
#                          $DATA_DIR/auth.json.
#
# Reads from setup_env: HERMES_RUNTIME_UID, HERMES_RUNTIME_GID, DATA_DIR.

install_config() {
  echo "==> Force-update config.yaml (provider change requires overwrite)"
  mkdir -p "$DATA_DIR"
  # Directory must be traversable by the hermes runtime UID; restrictive umasks
  # (e.g. 0077) have produced 0700 dirs that silently broke config reads.
  chmod 0755 "$DATA_DIR"
  # install honours an explicit mode regardless of the deploying shell's umask;
  # previous `cp` + best-effort chown left config.yaml owned by root mode 0600
  # under restrictive umasks, which the container UID 10000 could not read.
  install -m 0644 seed/hermes/config.yaml "$DATA_DIR/config.yaml"
  if ! chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR" "$DATA_DIR/config.yaml"; then
    echo "ERROR: chown to UID $HERMES_RUNTIME_UID failed for $DATA_DIR/config.yaml." >&2
    echo "       Hermes will silently fall back to defaults — every config.yaml override IGNORED." >&2
    echo "       Re-run this deploy as root (CAP_CHOWN required to change ownership across UIDs)." >&2
    exit 3
  fi
  # Final sanity check: the file must end up readable by UID 10000 once mounted.
  # A 0644 file owned by UID 10000 passes; so does 0644 owned by root since
  # others-read is set. Anything more restrictive is a regression.
  config_perms="$(stat -c '%a' "$DATA_DIR/config.yaml")"
  case "$config_perms" in
    *4|*5|*6|*7) : ;;  # others-read bit set
    *)
      echo "ERROR: $DATA_DIR/config.yaml mode is $config_perms — UID $HERMES_RUNTIME_UID cannot read it." >&2
      exit 3
      ;;
  esac
  echo "    config.yaml updated (mode 0644, owner $HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID)"
}

# Kanban DB schema migration ---------------------------------------------------
# Hermes >= the version that began referencing kanban session_id columns will
# log `sqlite3.OperationalError: no such column: session_id` every dispatcher
# tick (60s) against a kanban.db seeded by an older Hermes. Best-effort: add
# the column to the likely tables if missing. No-op when already present.
migrate_kanban_db() {
  local KANBAN_DB="$DATA_DIR/kanban.db"
  if [ -f "$KANBAN_DB" ]; then
    python3 - "$KANBAN_DB" <<'PY' || echo "    WARNING: kanban schema migration failed — dispatcher may continue to log errors"
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
added = []
existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
for table in ("tasks", "task_runs", "task_events", "kanban_notify_subs"):
    if table not in existing:
        continue
    cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    if "session_id" not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN session_id TEXT")
        added.append(table)
db.commit()
db.close()
if added:
    print("    kanban migration: added session_id to " + ", ".join(added))
else:
    print("    kanban migration: schema already up to date")
PY
    chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$KANBAN_DB" 2>/dev/null || true
  fi
}

# Per-tenant variant kept verbatim from the original additional-tenant block:
# different message prefix ("additional tenant kanban") and slightly different
# print expression. Behavior preserved for log parity.
migrate_kanban_db_for_tenant() {
  local tenant_kanban="$1"
  local tenant_uid="$2"
  local tenant_gid="$3"
  if [ -f "$tenant_kanban" ]; then
    python3 - "$tenant_kanban" <<'PY' || echo "    WARNING: additional tenant kanban migration failed"
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
added = []
existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
for table in ("tasks", "task_runs", "task_events", "kanban_notify_subs"):
    if table not in existing:
        continue
    cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    if "session_id" not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN session_id TEXT")
        added.append(table)
db.commit()
db.close()
print("    additional tenant kanban: added session_id to " + ", ".join(added) if added else "    additional tenant kanban: schema already up to date")
PY
    chown "$tenant_uid:$tenant_gid" "$tenant_kanban" 2>/dev/null || true
  fi
}

write_codex_auth() {
  echo "==> Write Codex / Hermes auth"
  if [ -s /tmp/codex-auth.b64 ]; then
    mkdir -p "$DATA_DIR/.codex"
    base64 -d /tmp/codex-auth.b64 > "$DATA_DIR/.codex/auth.json"
    chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/.codex" "$DATA_DIR/.codex/auth.json" 2>/dev/null || true
    chmod 600 "$DATA_DIR/.codex/auth.json"
    echo "    Codex auth.json written to $DATA_DIR/.codex/"

    # Hermes openai-codex does not read the raw Codex CLI auth shape directly.
    # Convert ~/.codex/auth.json tokens into the Hermes provider auth store.
    python3 - "$DATA_DIR/.codex/auth.json" "$DATA_DIR/auth.json" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

codex_auth_path = Path(sys.argv[1])
hermes_auth_path = Path(sys.argv[2])

codex_auth = json.loads(codex_auth_path.read_text())
tokens = codex_auth.get("tokens")
if not isinstance(tokens, dict):
    raise SystemExit("Codex auth.json is missing tokens")
for key in ("access_token", "refresh_token"):
    if not isinstance(tokens.get(key), str) or not tokens[key].strip():
        raise SystemExit(f"Codex auth.json is missing {key}")

try:
    hermes_auth = json.loads(hermes_auth_path.read_text()) if hermes_auth_path.exists() else {}
except Exception:
    hermes_auth = {}
if not isinstance(hermes_auth, dict):
    hermes_auth = {}

providers = hermes_auth.get("providers")
if not isinstance(providers, dict):
    providers = {}
    hermes_auth["providers"] = providers

state = providers.get("openai-codex")
if not isinstance(state, dict):
    state = {}
providers["openai-codex"] = state

state["tokens"] = tokens
state["last_refresh"] = (
    codex_auth.get("last_refresh")
    or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
)
state["auth_mode"] = "chatgpt"

hermes_auth_path.write_text(json.dumps(hermes_auth, indent=2, sort_keys=True) + "\n")
PY
    # Hard-fail on chown problems for the Hermes auth store — silent failure
    # here was the root cause of the 2026-05-25 production outage: file
    # remained root-owned after the python write_text, Hermes (uid 10000)
    # couldn't read it, and "Primary provider auth failed: No Codex
    # credentials stored" surfaced on every render. The .codex/auth.json
    # chown above can still be soft (its file isn't load-bearing for Hermes).
    if [ -z "${HERMES_RUNTIME_UID:-}" ] || [ -z "${HERMES_RUNTIME_GID:-}" ]; then
      echo "ERROR: HERMES_RUNTIME_UID/GID not set; cannot chown auth.json" >&2
      exit 1
    fi
    chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/auth.json" || {
      echo "ERROR: chown failed on $DATA_DIR/auth.json (uid=$HERMES_RUNTIME_UID gid=$HERMES_RUNTIME_GID)" >&2
      exit 1
    }
    chmod 600 "$DATA_DIR/auth.json"
    # Verify: Hermes provider auth depends on this file being owned by the
    # hermes runtime user. If we shipped a deploy where it isn't, render
    # turns will all fail with "No Codex credentials stored" until manual
    # recovery (docker exec -u root chown 10000:10000 /opt/data/auth.json).
    actual_uid="$(stat -c '%u' "$DATA_DIR/auth.json")"
    if [ "$actual_uid" != "$HERMES_RUNTIME_UID" ]; then
      echo "ERROR: auth.json owner mismatch after chown (expected $HERMES_RUNTIME_UID, got $actual_uid)" >&2
      exit 1
    fi
    echo "    Hermes openai-codex auth state written to $DATA_DIR/auth.json (owner $HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID)"
  else
    echo "    CODEX_AUTH_B64 not set — skipping auth.json"
  fi
}
