#!/usr/bin/env bash
# Runs ON the VPS via SSH. CI vars come from /tmp/ci-vars.env (scp'd by Actions).
set -euo pipefail

ROLLBACK_ON_ERROR=0
ROLLBACK_DONE=0
cleanup() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ] && [ "${ROLLBACK_ON_ERROR:-0}" = "1" ] && [ "${ROLLBACK_DONE:-0}" != "1" ]; then
    rollback_to_previous "script_error" || true
  fi
  rm -f /tmp/ci-vars.env /tmp/vps-env.b64 /tmp/codex-auth.b64 /tmp/vps-deploy.sh
}
trap cleanup EXIT

if command -v flock >/dev/null 2>&1; then
  exec 9>/tmp/botji-deploy.lock
  if ! flock -w 600 9; then
    echo "ERROR: another deploy is still running" >&2
    exit 1
  fi
fi

# Load CI variables
source /tmp/ci-vars.env
DEPLOY_PATH="${DEPLOY_PATH:-/opt/botji}"

echo "==> Deploy started on $(hostname) at $(date -u)"
echo "    Image: $IMAGE_REF"
echo "    Branch: $GIT_BRANCH"

cd "$DEPLOY_PATH"

PREVIOUS_GIT_HEAD="$(git rev-parse HEAD 2>/dev/null || true)"
PREVIOUS_IMAGE="$(docker inspect botji-hermes --format='{{.Config.Image}}' 2>/dev/null || true)"
echo "    Previous commit: ${PREVIOUS_GIT_HEAD:-none}"
echo "    Previous image: ${PREVIOUS_IMAGE:-none}"

echo "==> GHCR login"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

echo "==> Pull latest code"
git reset --hard HEAD
# The VPS checkout can contain previously scp'd/generated release files that
# later become tracked by the repo. Clean only repo-owned release trees before
# pulling so git can fast-forward without touching tenant data, .env, or logs.
git clean -fd -- \
  runtime/bin \
  seed/hermes/plugins \
  seed/hermes/skills \
  scripts/vps-postdeploy-smoke.sh
git pull --ff-only origin "$GIT_BRANCH"

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

set_env_var() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

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

echo "==> Pull image: $IMAGE_REF"
docker pull "$IMAGE_REF"

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

# ---- Kanban DB schema migration --------------------------------------------
# Hermes >= the version that began referencing kanban session_id columns will
# log `sqlite3.OperationalError: no such column: session_id` every dispatcher
# tick (60s) against a kanban.db seeded by an older Hermes. Best-effort: add
# the column to the likely tables if missing. No-op when already present.
KANBAN_DB="$DATA_DIR/kanban.db"
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

echo "==> Bootstrap seed (idempotent — skips existing files)"
export BOTJI_PROD_IMAGE="$IMAGE_REF"
# Ensure workspace is writable by the hermes user (UID 10000) before bootstrap runs
mkdir -p "$WORKSPACE_DIR"
chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$WORKSPACE_DIR" 2>/dev/null || true
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile bootstrap run --rm bootstrap

sync_code_components() {
  # Code artefacts — always reseed from the checked-out release. The glob-based
  # approach means new plugins/skills ship automatically without editing this script.
  mkdir -p "$DATA_DIR/skills" "$DATA_DIR/plugins" "$DATA_DIR/prompts" "$DATA_DIR/schemas"
  for skill_dir in seed/hermes/skills/botji-*; do
    [ -d "$skill_dir" ] || continue
    skill_name="$(basename "$skill_dir")"
    rm -rf "$DATA_DIR/skills/$skill_name"
    cp -R "$skill_dir" "$DATA_DIR/skills/" 2>/dev/null || true
  done
  for plugin_dir in seed/hermes/plugins/botji-*; do
    [ -d "$plugin_dir" ] || continue
    [ -f "$plugin_dir/plugin.yaml" ] && [ -f "$plugin_dir/__init__.py" ] || continue
    plugin_name="$(basename "$plugin_dir")"
    valid_plugins="${valid_plugins:-} $plugin_name"
    rm -rf "$DATA_DIR/plugins/$plugin_name"
    cp -R "$plugin_dir" "$DATA_DIR/plugins/"
    echo "    seeded plugin: $plugin_name"
  done
  for deployed_plugin in "$DATA_DIR"/plugins/botji-*; do
    [ -d "$deployed_plugin" ] || continue
    plugin_name="$(basename "$deployed_plugin")"
    case " ${valid_plugins:-} " in
      *" $plugin_name "*) ;;
      *)
        rm -rf "$deployed_plugin"
        echo "    removed stale plugin: $plugin_name"
        ;;
    esac
  done
  # seed/hermes/prompts/ is optional — runtime prompts now live under each plugin
  # (botji-artifacts/prompts/*.md) and are loaded via _prompts.load_prompt(). The
  # top-level prompts/templates/ tree was unused and removed 2026-05-21. Keep the
  # data dir clean for any tenants that still have stale templates copied over.
  rm -rf "$DATA_DIR/prompts"
  if [ -d seed/hermes/prompts ]; then
    cp -R seed/hermes/prompts "$DATA_DIR/"
  fi
  cp seed/hermes/schemas/*.json "$DATA_DIR/schemas/" 2>/dev/null || true
  chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" \
    "$DATA_DIR/plugins" "$DATA_DIR/skills" "$DATA_DIR/prompts" "$DATA_DIR/schemas" 2>/dev/null || true
  mkdir -p "$DATA_DIR/verdicts"
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/verdicts" 2>/dev/null || true
}

rollback_to_previous() {
  ROLLBACK_DONE=1
  local failed_status="$1"
  echo "==> Rolling back after failed deploy ($failed_status)" >&2

  if [ -n "${PREVIOUS_GIT_HEAD:-}" ]; then
    git reset --hard "$PREVIOUS_GIT_HEAD" || true
    sync_code_components || true
    echo "    Repo and code components restored to $PREVIOUS_GIT_HEAD" >&2
  fi

  if [ -n "${PREVIOUS_IMAGE:-}" ]; then
    export BOTJI_PROD_IMAGE="$PREVIOUS_IMAGE"
    docker compose -f docker-compose.yml -f docker-compose.prod.yml \
      up -d --force-recreate --no-build --remove-orphans || true
    for _ in $(seq 1 15); do
      ROLLBACK_STATUS=$(docker inspect botji-hermes \
        --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")
      [ "$ROLLBACK_STATUS" = "healthy" ] && break
      sleep 4
    done
    echo "    Rollback status: ${ROLLBACK_STATUS:-unknown}" >&2
    [ "${ROLLBACK_STATUS:-}" = "healthy" ] || docker logs botji-hermes --tail 40 >&2 || true
  else
    echo "    No previous image available; manual intervention required" >&2
  fi
}

ROLLBACK_ON_ERROR=1

echo "==> Force-update code components (plugin, skills, schemas, prompts)"
sync_code_components
echo "    Plugins, skills, schemas, prompts updated from seed."

echo "==> Pre-flight plugin smoke test"
# Imports every plugin's submodules + asserts public symbols exist. Aborts the
# deploy before container start if any plugin is broken — prevents silent outages
# where bad imports ship to prod and the agent bypasses the fidelity harness.
if [ -x scripts/smoke-plugin.sh ]; then
  if ! PLUGINS_ROOT="$DATA_DIR/plugins" bash scripts/smoke-plugin.sh; then
    echo "ERROR: Plugin smoke test FAILED — aborting deploy before container start." >&2
    echo "       Run locally: PLUGINS_ROOT=seed/hermes/plugins bash scripts/smoke-plugin.sh" >&2
    exit 2
  fi
else
  echo "    WARNING: scripts/smoke-plugin.sh missing — skipping pre-flight smoke."
fi

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
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/auth.json" 2>/dev/null || true
  chmod 600 "$DATA_DIR/auth.json"
  echo "    Hermes openai-codex auth state written to $DATA_DIR/auth.json"
else
  echo "    CODEX_AUTH_B64 not set — skipping auth.json"
fi

echo "==> Clear Telegram webhook (ensures clean long-polling)"
BOT_TOKEN="$(grep -E '^TELEGRAM_BOT_TOKEN=' .env 2>/dev/null | cut -d= -f2 | tr -d "'" | tr -d '"')"
if [ -n "$BOT_TOKEN" ]; then
  curl -s "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true" | grep -o '"ok":[^,}]*' || true
  echo "    Webhook cleared"
fi

echo "==> Start / reload"
# Defensive: free the container_name compose is about to claim. `--remove-orphans`
# only prunes within the current compose project, so a container left behind by
# a previous deploy under a different project/tenant prefix silently squats on
# the name and `compose up` fails with `Conflict. The container name is already
# in use`. We've seen this when BOTJI_TENANT_ID drifted (botji vs botji-single-
# tenant); the would-be name is whatever ${BOTJI_TENANT_ID:-botji}-hermes
# resolves to, plus the historical bare `botji-hermes`.
EXPECTED_CONTAINER="${BOTJI_TENANT_ID:-botji}-hermes"
for name in botji-hermes "$EXPECTED_CONTAINER"; do
  if docker inspect "$name" >/dev/null 2>&1; then
    echo "    pre-existing container '$name' found — removing to free the name"
    docker rm -f "$name" >/dev/null 2>&1 || true
  fi
done
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --force-recreate --no-build --remove-orphans

echo "==> Waiting for healthy..."
for _ in $(seq 1 15); do
  STATUS=$(docker inspect botji-hermes \
    --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")
  [ "$STATUS" = "healthy" ] && break
  sleep 4
done

echo "==> Smoke check"
sleep 10
STATUS=$(docker inspect botji-hermes \
  --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")
echo "    Status: $STATUS"
if [ "$STATUS" = "healthy" ]; then
  if [ -f scripts/vps-postdeploy-smoke.sh ]; then
    echo "==> Post-deploy smoke"
    BOTJI_CONTAINER_NAME="botji-hermes" \
      BOTJI_TENANT_ID="${BOTJI_TENANT_ID:-botji}" \
      bash scripts/vps-postdeploy-smoke.sh
  else
    echo "    WARNING: scripts/vps-postdeploy-smoke.sh missing - skipping post-deploy smoke."
  fi
  mkdir -p "$DATA_DIR/.botji"
  cat > "$DATA_DIR/.botji/LAST_DEPLOY.json" <<EOF
{
  "deployed_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "git_branch": "$GIT_BRANCH",
  "git_commit": "$(git rev-parse HEAD)",
  "image_ref": "$IMAGE_REF",
  "container_health": "$STATUS"
}
EOF
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/.botji/LAST_DEPLOY.json" 2>/dev/null || true
  ROLLBACK_ON_ERROR=0
  echo "==> Deploy complete."
else
  echo "ERROR: Container not healthy ($STATUS)" >&2
  docker logs botji-hermes --tail 40 >&2 || true
  rollback_to_previous "$STATUS"
  exit 1
fi

# ----------------------------------------------------------------------------
# Additional tenants on the same box
# ----------------------------------------------------------------------------
# /opt/botji is the CI-tracked tenant deployed above (it owns the git checkout
# and runs the auth/.env writes from CI secrets). Other tenants (degain, etc.)
# are siblings — same GHCR image, same skills/plugins, but their own .env,
# config.yaml, .codex/auth.json, Telegram bot, and allowlist. We sync code
# components (plugins/skills/schemas) from /opt/botji's checkout into each
# additional tenant's data dir and restart their containers with the new image.
# We do NOT touch their .env, config.yaml, or .codex/auth.json — those are
# tenant-specific and intentionally diverge.
#
# Failures in an additional tenant are logged as warnings but do NOT fail the
# overall deploy: /opt/botji is already healthy at this point and the workflow
# should not roll back a healthy primary because a sibling tenant had trouble.
deploy_additional_tenant() {
  local tenant_path="$1"
  if [ ! -d "$tenant_path" ]; then
    echo "==> Additional tenant $tenant_path: directory missing, skipping"
    return 0
  fi
  if [ ! -f "$tenant_path/.env" ]; then
    echo "==> Additional tenant $tenant_path: .env missing, skipping"
    return 0
  fi

  echo "==> Additional tenant deploy: $tenant_path"

  local tenant_data_dir tenant_id tenant_uid tenant_gid
  tenant_data_dir="$(grep -E '^BOTJI_DATA_DIR=' "$tenant_path/.env" 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'\"")"
  tenant_id="$(grep -E '^BOTJI_TENANT_ID=' "$tenant_path/.env" 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'\"")"
  tenant_uid="$(grep -E '^HERMES_UID=' "$tenant_path/.env" 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'\"")"
  tenant_gid="$(grep -E '^HERMES_GID=' "$tenant_path/.env" 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'\"")"
  tenant_data_dir="${tenant_data_dir:-./data/$tenant_id}"
  tenant_uid="${tenant_uid:-10000}"
  tenant_gid="${tenant_gid:-$tenant_uid}"
  local tenant_container="${tenant_id}-hermes"
  local tenant_data_abs="$tenant_path/${tenant_data_dir#./}"

  echo "    tenant=$tenant_id container=$tenant_container data=$tenant_data_abs"

  # Sync plugins / skills / schemas from the primary's checkout into the
  # additional tenant's data dir. Reuses sync_code_components by overriding DATA_DIR
  # + HERMES_RUNTIME_UID/GID, then restoring the primary values.
  local saved_data_dir="$DATA_DIR"
  local saved_uid="$HERMES_RUNTIME_UID"
  local saved_gid="$HERMES_RUNTIME_GID"
  DATA_DIR="$tenant_data_abs"
  HERMES_RUNTIME_UID="$tenant_uid"
  HERMES_RUNTIME_GID="$tenant_gid"
  sync_code_components || echo "    WARNING: additional tenant code sync had non-fatal errors"
  DATA_DIR="$saved_data_dir"
  HERMES_RUNTIME_UID="$saved_uid"
  HERMES_RUNTIME_GID="$saved_gid"
  echo "    code components synced (plugins, skills, schemas)"

  # Best-effort kanban schema migration on the additional tenant's kanban.db.
  local tenant_kanban="$tenant_data_abs/kanban.db"
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

  # Remove stale skills metadata files written by a previous (different) image.
  # When a tenant transitions from one hermes-agent image to another, the old
  # image's ~/.hermes/skills/.bundled_manifest is read by the new image's
  # skills_sync.py at startup and can fail with PermissionError or format
  # mismatch — observed 2026-05-22 when degain transitioned from
  # phase0-fidelity-20260520073731 to the GHCR image and crashed in a restart
  # loop. The bundled_manifest is regenerated on every container start, so it
  # is safe to drop. .skills_prompt_snapshot.json is similar.
  for stale in "$tenant_data_abs/skills/.bundled_manifest" "$tenant_data_abs/.skills_prompt_snapshot.json"; do
    if [ -f "$stale" ]; then
      rm -f "$stale"
      echo "    removed stale image-specific metadata: $stale"
    fi
  done

  # Ensure the tenant data tree is traversable by the hermes UID inside the
  # container. Restrictive umasks during prior deploys leave dirs as 0700 —
  # in theory still traversable by the matching owner UID, but PRs #20 and
  # #21 both showed degain crash-looping on PermissionError stat'ing
  # /opt/data/skills/.bundled_manifest until the data root was widened to
  # 755. Chmod the data root itself (non-recursive) and skills/plugins
  # subtrees (-R). go+rX expands directory traversal without granting write,
  # and capital X only sets x where it already exists or on dirs — so
  # secrets (.env at 0600, .codex/auth.json at 0600) stay 0600.
  chmod u+rwX,go+rX "$tenant_data_abs" 2>/dev/null || true
  chmod -R u+rwX,go+rX \
    "$tenant_data_abs/skills" \
    "$tenant_data_abs/plugins" 2>/dev/null || true

  # Recreate the additional tenant container with the new image. The additional tenant's compose
  # file resolves container_name from its own .env (BOTJI_TENANT_ID=degain →
  # degain-hermes), so we just need to be in its directory and pass the image
  # via BOTJI_PROD_IMAGE.
  (
    cd "$tenant_path"
    export BOTJI_PROD_IMAGE="$IMAGE_REF"
    export HERMES_UID="$tenant_uid"
    export HERMES_GID="$tenant_gid"
    export BOTJI_TENANT_ID="$tenant_id"
    if docker inspect "$tenant_container" >/dev/null 2>&1; then
      docker rm -f "$tenant_container" >/dev/null 2>&1 || true
    fi
    docker compose -f docker-compose.yml -f docker-compose.prod.yml \
      up -d --force-recreate --no-build --remove-orphans
  ) || { echo "    WARNING: additional tenant compose up failed — additional tenant left unchanged"; return 0; }

  # Wait for healthy, warn on failure but do NOT fail the primary deploy.
  local tenant_status="not_found"
  for _ in $(seq 1 15); do
    tenant_status="$(docker inspect "$tenant_container" --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")"
    [ "$tenant_status" = "healthy" ] && break
    sleep 4
  done
  if [ "$tenant_status" = "healthy" ]; then
    echo "    additional tenant $tenant_container healthy"
    if [ -f scripts/vps-postdeploy-smoke.sh ]; then
      BOTJI_CONTAINER_NAME="$tenant_container" \
        BOTJI_TENANT_ID="$tenant_id" \
        bash scripts/vps-postdeploy-smoke.sh \
        || echo "    WARNING: $tenant_container post-deploy smoke failed (non-fatal — /opt/botji already shipped)"
    fi
  else
    echo "    WARNING: $tenant_container unhealthy ($tenant_status) — /opt/botji deploy already succeeded"
    docker logs "$tenant_container" --tail 30 2>&1 | sed 's/^/      | /' || true
  fi
}

# Run additional-tenant deploys as a non-fatal post-step. List tenants here
# (whitespace-separated under BOTJI_ADDITIONAL_TENANTS, default = degain).
BOTJI_ADDITIONAL_TENANTS="${BOTJI_ADDITIONAL_TENANTS:-/opt/botji-degain}"
for tenant_path in $BOTJI_ADDITIONAL_TENANTS; do
  deploy_additional_tenant "$tenant_path" || true
done

# Prune images not used by any running container. Prevents the ~30 GB disk
# accumulation seen after each CI deploy (old sha-* images pile up as <none>).
echo "==> Pruning unused images"
docker image prune -a -f 2>/dev/null || true

# Cleanup
rm -f /tmp/ci-vars.env /tmp/vps-env.b64 /tmp/codex-auth.b64 /tmp/vps-deploy.sh
