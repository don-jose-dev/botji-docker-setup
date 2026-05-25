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
#   write_codex_auth     — preserve the VPS-local Codex OAuth chain unless a
#                          staged CI seed is newer/forced, then convert into
#                          the Hermes provider auth store at $DATA_DIR/auth.json.
#   ensure_auth_file_owner DATA UID GID LABEL
#                        — harden $DATA/auth.json ownership/mode after startup.
#   write_hermes_auth_store_from_codex CODEX_AUTH HERMES_AUTH
#                        — convert a Codex CLI auth file into the Hermes
#                          openai-codex provider store shape.
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

ensure_auth_file_owner() {
  local data_dir="$1"
  local runtime_uid="$2"
  local runtime_gid="$3"
  local label="${4:-Hermes auth}"

  if [ ! -f "$data_dir/auth.json" ]; then
    return 0
  fi
  if [ -z "$runtime_uid" ] || [ -z "$runtime_gid" ]; then
    echo "ERROR: runtime UID/GID not set; cannot chown $data_dir/auth.json" >&2
    return 1
  fi
  chown "$runtime_uid:$runtime_gid" "$data_dir/auth.json" || {
    echo "ERROR: chown failed on $data_dir/auth.json (uid=$runtime_uid gid=$runtime_gid)" >&2
    return 1
  }
  chmod 600 "$data_dir/auth.json"

  actual_uid="$(stat -c '%u' "$data_dir/auth.json")"
  if [ "$actual_uid" != "$runtime_uid" ]; then
    echo "ERROR: auth.json owner mismatch after chown (expected $runtime_uid, got $actual_uid)" >&2
    return 1
  fi
  echo "    $label auth.json owner ensured ($runtime_uid:$runtime_gid)"
}

ensure_codex_auth_file_owner() {
  local codex_auth_path="$1"
  local runtime_uid="$2"
  local runtime_gid="$3"
  local label="${4:-Codex CLI}"
  local codex_home

  if [ ! -f "$codex_auth_path" ]; then
    return 0
  fi
  codex_home="$(dirname "$codex_auth_path")"
  if [ -z "$runtime_uid" ] || [ -z "$runtime_gid" ]; then
    echo "ERROR: runtime UID/GID not set; cannot chown $codex_auth_path" >&2
    return 1
  fi
  chown "$runtime_uid:$runtime_gid" "$codex_home" "$codex_auth_path" || {
    echo "ERROR: chown failed on $codex_auth_path (uid=$runtime_uid gid=$runtime_gid)" >&2
    return 1
  }
  chmod 700 "$codex_home"
  chmod 600 "$codex_auth_path"

  actual_uid="$(stat -c '%u' "$codex_auth_path")"
  if [ "$actual_uid" != "$runtime_uid" ]; then
    echo "ERROR: Codex auth owner mismatch after chown (expected $runtime_uid, got $actual_uid)" >&2
    return 1
  fi
  echo "    $label Codex auth owner ensured ($runtime_uid:$runtime_gid)"
}

validate_codex_auth_file() {
  local codex_auth_path="$1"
  python3 - "$codex_auth_path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())
tokens = data.get("tokens")
if not isinstance(tokens, dict):
    raise SystemExit("Codex auth.json is missing tokens")
for key in ("access_token", "refresh_token"):
    if not isinstance(tokens.get(key), str) or not tokens[key].strip():
        raise SystemExit(f"Codex auth.json is missing {key}")
PY
}

codex_auth_refresh_epoch() {
  local codex_auth_path="$1"
  python3 - "$codex_auth_path" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text())
except Exception:
    print(0)
    raise SystemExit(0)

raw = data.get("last_refresh")
if not isinstance(raw, str) or not raw.strip():
    print(0)
    raise SystemExit(0)

value = raw.strip()
if value.endswith("Z"):
    value = value[:-1] + "+00:00"
try:
    parsed = datetime.fromisoformat(value)
except ValueError:
    print(0)
    raise SystemExit(0)
if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
print(int(parsed.timestamp()))
PY
}

maybe_install_staged_codex_auth() {
  local staged_b64="$1"
  local codex_auth_path="$2"
  local runtime_uid="$3"
  local runtime_gid="$4"
  local label="${5:-Codex}"
  local force="${BOTJI_FORCE_CODEX_AUTH_SYNC:-0}"
  local tmp
  local staged_epoch=0
  local live_epoch=0

  if [ ! -s "$staged_b64" ]; then
    return 1
  fi

  tmp="$(TMPDIR="${TMPDIR:-/tmp}" mktemp)"
  if ! base64 -d "$staged_b64" > "$tmp"; then
    rm -f "$tmp"
    echo "ERROR: failed to decode staged $label Codex auth seed" >&2
    return 2
  fi
  if ! validate_codex_auth_file "$tmp"; then
    rm -f "$tmp"
    echo "ERROR: staged $label Codex auth seed is invalid" >&2
    return 2
  fi

  if [ -f "$codex_auth_path" ] && [ "$force" != "1" ]; then
    if ! staged_epoch="$(codex_auth_refresh_epoch "$tmp")"; then
      staged_epoch=0
    fi
    if ! live_epoch="$(codex_auth_refresh_epoch "$codex_auth_path")"; then
      live_epoch=0
    fi

    # Codex OAuth refresh tokens rotate. A GitHub secret can become older than
    # the VPS-local token chain after Hermes refreshes successfully; replaying
    # that older seed on the next deploy reintroduces refresh_token_reused.
    if [ "$live_epoch" -gt 0 ] && { [ "$staged_epoch" -eq 0 ] || [ "$staged_epoch" -le "$live_epoch" ]; }; then
      rm -f "$tmp"
      echo "    $label Codex auth preserved (live OAuth chain is newer/equal than staged CI seed)"
      return 1
    fi
  fi

  mkdir -p "$(dirname "$codex_auth_path")"
  install -m 600 "$tmp" "$codex_auth_path"
  rm -f "$tmp"
  ensure_codex_auth_file_owner "$codex_auth_path" "$runtime_uid" "$runtime_gid" "$label" || return 2
  if [ "$force" = "1" ]; then
    echo "    $label Codex auth installed from staged CI seed (forced)"
  else
    echo "    $label Codex auth installed from staged CI seed"
  fi
  return 0
}

write_hermes_auth_store_from_codex() {
  local codex_auth_path="$1"
  local hermes_auth_path="$2"

  python3 - "$codex_auth_path" "$hermes_auth_path" <<'PY'
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
}

write_codex_auth() {
  echo "==> Write Codex / Hermes auth"
  local codex_auth_path="$DATA_DIR/.codex/auth.json"

  maybe_install_staged_codex_auth \
    /tmp/codex-auth.b64 \
    "$codex_auth_path" \
    "$HERMES_RUNTIME_UID" \
    "$HERMES_RUNTIME_GID" \
    "primary" || {
      case "$?" in
        1) : ;;
        *) return 1 ;;
      esac
    }

  if [ -f "$codex_auth_path" ]; then
    ensure_codex_auth_file_owner "$codex_auth_path" "$HERMES_RUNTIME_UID" "$HERMES_RUNTIME_GID" "primary" || return 1
    # Hermes openai-codex does not read the raw Codex CLI auth shape directly.
    # Convert ~/.codex/auth.json tokens into the Hermes provider auth store.
    write_hermes_auth_store_from_codex "$codex_auth_path" "$DATA_DIR/auth.json"
    # Hard-fail on chown problems for the Hermes auth store — silent failure
    # here was the root cause of the 2026-05-25 production outage: file
    # remained root-owned after the python write_text, Hermes (uid 10000)
    # couldn't read it, and "Primary provider auth failed: No Codex
    # credentials stored" surfaced on every render.
    ensure_auth_file_owner "$DATA_DIR" "$HERMES_RUNTIME_UID" "$HERMES_RUNTIME_GID" "Hermes openai-codex"
  elif [ -f "$DATA_DIR/auth.json" ]; then
    echo "    no Codex CLI auth file present; preserving existing Hermes provider auth store"
    ensure_auth_file_owner "$DATA_DIR" "$HERMES_RUNTIME_UID" "$HERMES_RUNTIME_GID" "Hermes openai-codex"
  else
    echo "ERROR: no Codex auth present for primary tenant; run a fresh Codex login and stage CODEX_AUTH_B64" >&2
    return 1
  fi
}
