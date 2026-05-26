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
EXPECTED_TERMINAL_CWD="${BOTJI_EXPECTED_TERMINAL_CWD:-/workspace}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXPECTED_SKILL_NAMES="${BOTJI_EXPECTED_SKILL_NAMES:-}"
if [ -z "$EXPECTED_SKILL_NAMES" ] && [ -d "$REPO_ROOT/seed/hermes/skills" ]; then
  EXPECTED_SKILL_NAMES="$(find "$REPO_ROOT/seed/hermes/skills" -mindepth 1 -maxdepth 1 -type d -name 'botji-*' -printf '%f\n' | sort | paste -sd, -)"
fi
EXPECTED_PLUGIN_NAMES="${BOTJI_EXPECTED_PLUGIN_NAMES:-}"
if [ -z "$EXPECTED_PLUGIN_NAMES" ] && [ -d "$REPO_ROOT/seed/hermes/plugins" ]; then
  EXPECTED_PLUGIN_NAMES="$(find "$REPO_ROOT/seed/hermes/plugins" -mindepth 1 -maxdepth 1 -type d -name 'botji-*' -printf '%f\n' | sort | paste -sd, -)"
fi
EXPECTED_PROMPT_RELS="${BOTJI_EXPECTED_PROMPT_RELS:-}"
if [ -z "$EXPECTED_PROMPT_RELS" ] && [ -d "$REPO_ROOT/seed/hermes/plugins" ]; then
  EXPECTED_PROMPT_RELS="$(find "$REPO_ROOT/seed/hermes/plugins" -path '*/prompts/*.md' -printf '%P\n' | sort | paste -sd, -)"
fi
SINCE_MINUTES="${BOTJI_BUDGET_SINCE_MINUTES:-30}"
MAX_RESPONSE_SECONDS="${BOTJI_MAX_RESPONSE_SECONDS:-180}"
MAX_API_CALLS="${BOTJI_MAX_API_CALLS:-8}"
MAX_SESSION_API_CALLS="${BOTJI_MAX_SESSION_API_CALLS:-14}"
MAX_SESSION_TOOL_TURNS="${BOTJI_MAX_SESSION_TOOL_TURNS:-14}"

echo "=== postdeploy: container health ==="
docker inspect "$CONTAINER_NAME" --format='{{.Name}} started={{.State.StartedAt}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
STARTED_AT="$(docker inspect "$CONTAINER_NAME" --format='{{.State.StartedAt}}')"

# Cap the budget-check window to the container's current uptime. Without this
# cap, a fresh deploy is judged by traffic from BEFORE the deploy — including
# the regressions the deploy was meant to fix — and rolls back its own success.
# Seen in production 2026-05-22: a pre-deploy session that ran 5 transforms
# (the exact pattern the canonical retry-cap PR was fixing) rolled back the
# very deploy that shipped that fix.
if STARTED_EPOCH="$(date -d "$STARTED_AT" +%s 2>/dev/null)"; then
  NOW_EPOCH="$(date +%s)"
  RESTART_AGE_MIN=$(( (NOW_EPOCH - STARTED_EPOCH + 59) / 60 ))
  [ "$RESTART_AGE_MIN" -lt 1 ] && RESTART_AGE_MIN=1
  if [ "$RESTART_AGE_MIN" -lt "$SINCE_MINUTES" ]; then
    echo "    budget window scoped to container age: ${RESTART_AGE_MIN}m (default ${SINCE_MINUTES}m)"
    SINCE_MINUTES="$RESTART_AGE_MIN"
  fi
fi

echo "=== postdeploy: gateway runtime contract ==="
docker exec \
  -e EXPECTED_TENANT="$TENANT_ID" \
  -e EXPECTED_TERMINAL_CWD="$EXPECTED_TERMINAL_CWD" \
  -u 10000:10000 "$CONTAINER_NAME" sh -lc '
set -eu
pids="$(ps -eo pid=,args= | awk "/\/python[0-9.]* / && /\/hermes gateway run$/ {print \$1}")"
count="$(printf "%s\n" "$pids" | sed "/^$/d" | wc -l | tr -d " ")"
if [ "$count" != "1" ]; then
  echo "ERROR: expected exactly one hermes gateway run process, found $count" >&2
  ps -eo pid,ppid,user,args | grep -E "[h]ermes gateway run|[s]6-supervise gateway" >&2 || true
  exit 1
fi
pid="$(printf "%s\n" "$pids" | sed "/^$/d" | head -n1)"
env_dump="$(tr "\0" "\n" < "/proc/$pid/environ")"
require_env() {
  if ! printf "%s\n" "$env_dump" | grep -q "^$1="; then
    echo "ERROR: gateway process missing env $1" >&2
    exit 1
  fi
}
for key in HERMES_HOME CODEX_HOME XDG_STATE_HOME BOTJI_TENANT_ID TERMINAL_CWD; do
  require_env "$key"
done
if ! printf "%s\n" "$env_dump" | grep -qx "BOTJI_TENANT_ID=$EXPECTED_TENANT"; then
  echo "ERROR: gateway BOTJI_TENANT_ID does not match $EXPECTED_TENANT" >&2
  exit 1
fi
if ! printf "%s\n" "$env_dump" | grep -qx "TERMINAL_CWD=$EXPECTED_TERMINAL_CWD"; then
  echo "ERROR: gateway TERMINAL_CWD does not match $EXPECTED_TERMINAL_CWD" >&2
  exit 1
fi
proc_cwd="$(readlink "/proc/$pid/cwd")"
if [ "$proc_cwd" != "$EXPECTED_TERMINAL_CWD" ]; then
  echo "ERROR: gateway cwd is $proc_cwd, expected $EXPECTED_TERMINAL_CWD" >&2
  exit 1
fi
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  require_env TELEGRAM_BOT_TOKEN
fi
python3 - <<'"'"'PY'"'"'
import json
from pathlib import Path

auth_path = Path("/opt/data/auth.json")
if not auth_path.exists():
    raise SystemExit("ERROR: /opt/data/auth.json missing")
data = json.loads(auth_path.read_text())
tokens = (
    data.get("providers", {})
    .get("openai-codex", {})
    .get("tokens", {})
)
missing = [key for key in ("access_token", "refresh_token") if not tokens.get(key)]
if missing:
    raise SystemExit("ERROR: Codex provider auth missing " + ",".join(missing))
PY
echo "    gateway process/env/auth contract ok"
'

echo "=== postdeploy: seeded skills/plugins/prompts ==="
docker exec -i \
  -e EXPECTED_SKILL_NAMES="$EXPECTED_SKILL_NAMES" \
  -e EXPECTED_PLUGIN_NAMES="$EXPECTED_PLUGIN_NAMES" \
  -e EXPECTED_PROMPT_RELS="$EXPECTED_PROMPT_RELS" \
  -u 10000:10000 "$CONTAINER_NAME" python3 - <<'PY'
import os
from pathlib import Path


def split_csv(name: str) -> list[str]:
    return [item for item in os.environ.get(name, "").split(",") if item]

skills = split_csv("EXPECTED_SKILL_NAMES")
plugins = split_csv("EXPECTED_PLUGIN_NAMES")
prompts = split_csv("EXPECTED_PROMPT_RELS")
if not skills or not plugins or not prompts:
    raise SystemExit("ERROR: seed inventory missing; cannot verify skills/plugins/prompts")

missing: list[str] = []
for skill in skills:
    path = Path("/opt/data/skills") / skill / "SKILL.md"
    if not path.is_file() or not path.read_text(errors="replace").strip():
        missing.append(str(path))
for plugin in plugins:
    root = Path("/opt/data/plugins") / plugin
    for name in ("plugin.yaml", "__init__.py"):
        path = root / name
        if not path.is_file():
            missing.append(str(path))
for rel in prompts:
    path = Path("/opt/data/plugins") / rel
    if not path.is_file() or not path.read_text(errors="replace").strip():
        missing.append(str(path))
if missing:
    raise SystemExit("ERROR: missing/unreadable seeded files:\n" + "\n".join(missing))
print(f"    skills={len(skills)} plugins={len(plugins)} prompts={len(prompts)} ok")
PY


echo "=== postdeploy: Hermes provider/platform status ==="
docker exec -u 10000:10000 "$CONTAINER_NAME" sh -lc '
set -eu
export HERMES_HOME="${HERMES_HOME:-/opt/data}"
export CODEX_HOME="${CODEX_HOME:-/opt/data/.codex}"
export XDG_STATE_HOME="${XDG_STATE_HOME:-/opt/data/.local/state}"
if [ -x /opt/hermes/.venv/bin/hermes ]; then
  HERMES_BIN=/opt/hermes/.venv/bin/hermes
elif command -v hermes >/dev/null 2>&1; then
  HERMES_BIN=hermes
else
  echo "ERROR: hermes binary not found" >&2
  exit 1
fi
status_output="$("$HERMES_BIN" status 2>&1)" || {
  printf "%s\n" "$status_output" >&2
  exit 1
}
printf "%s\n" "$status_output"
printf "%s\n" "$status_output" | awk "
  /OpenAI Codex/ && /logged in/ && !/not logged/ { ok=1 }
  END { exit ok ? 0 : 1 }
" || {
  echo "ERROR: OpenAI Codex provider is not logged in" >&2
  exit 1
}
printf "%s\n" "$status_output" | awk "
  /Telegram/ && /configured/ && !/not configured/ { ok=1 }
  END { exit ok ? 0 : 1 }
" || {
  echo "ERROR: Telegram platform is not configured" >&2
  exit 1
}
'

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

echo "=== postdeploy: consolidated harness (core+gate+allowlist+contract+fidelity-guard+id-family) ==="
# Ship the deterministic fixtures into the container so the runner can
# discover them via BOTJI_HARNESS_FIXTURES — Dockerfile/deploy do not seed them,
# and we deliberately keep the harness binary stateless of fixture data.
#
# The destination MUST NOT be /tmp on this image: /tmp is mounted as tmpfs with
# noexec (observed 2026-05-22 on the production VPS), which causes `docker cp`
# to fail silently — exit 0, target dir never created, harness reports
# "no fixtures found" while smoke claims success. The persistent /opt/data mount
# is the safe target. The trailing `/.` on SRC tells docker cp to copy the
# directory's CONTENTS rather than the directory itself, so suite subdirs land
# at $FIXTURES_DEST/<suite>/ — matching the harness's _fixtures_root() layout.
# Fixture-based suites are dev-test correctness, not production health: a
# fixture regression should NOT roll back a healthy deploy. The other smoke
# stages (runtime tenant harness above, artifact mini-harness + log budget
# below) still fail-fast on real production issues. Harness output is logged
# to surface failures in the deploy run; a WARN line makes them grep-able.
FIXTURES_SRC="$(cd "$(dirname "$0")/.." && pwd)/tests/fixtures"
FIXTURES_DEST="/opt/data/.botji-harness-fixtures"
if [ -d "$FIXTURES_SRC" ]; then
  docker exec "$CONTAINER_NAME" rm -rf "$FIXTURES_DEST"
  docker exec "$CONTAINER_NAME" mkdir -p "$FIXTURES_DEST"
  docker cp "$FIXTURES_SRC/." "$CONTAINER_NAME:$FIXTURES_DEST"
  if ! docker exec -e BOTJI_HARNESS_FIXTURES="$FIXTURES_DEST" \
        "$CONTAINER_NAME" botji-harness run --suite all; then
    echo "    WARN: consolidated harness reported fixture failures (advisory; deploy continues)"
  fi
  docker exec "$CONTAINER_NAME" rm -rf "$FIXTURES_DEST"
else
  echo "    WARNING: $FIXTURES_SRC missing — running without fixtures"
  docker exec "$CONTAINER_NAME" botji-harness run --suite all || \
    echo "    WARN: harness exited non-zero without fixtures (advisory)"
fi

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
  --max-tool-count operation_run=1 \
  --max-tool-count review_record=1 \
  --max-tool-seconds operation_run=90 \
  --max-tool-seconds evidence_extract_manifest=90

if [ "${RUN_LIVE_PROVIDER_E2E:-0}" = "1" ]; then
  echo "=== postdeploy: live provider e2e ==="
  docker exec "$CONTAINER_NAME" botji-artifact-e2e \
    --quality low \
    --max-attempts "${BOTJI_LIVE_E2E_MAX_ATTEMPTS:-1}" \
    --require-pass
else
  echo "=== postdeploy: live provider e2e skipped (RUN_LIVE_PROVIDER_E2E=0) ==="
fi
