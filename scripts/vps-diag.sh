#!/usr/bin/env bash
# Runs ON the VPS via SSH. Focused diagnostic: message timing, steps, model used.
set -euo pipefail

redact_stream() {
  python3 -c '
import re
import sys

patterns = [
    (re.compile(r"(?i)(\b[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|AUTH)[A-Z0-9_]*=)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\"?(?:access|refresh|id)_token\"?\s*[:=]\s*\")[^\"]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._-]+"), r"\1[REDACTED]"),
    (re.compile(r"\b(?:bot)?\d{6,12}:[A-Za-z0-9_-]{25,}\b"), "[REDACTED_TELEGRAM_TOKEN]"),
    (re.compile(r"\b(?:sk|sess)-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_SECRET]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED_JWT]"),
]

for line in sys.stdin:
    for pattern, replacement in patterns:
        line = pattern.sub(replacement, line)
    sys.stdout.write(line)
    sys.stdout.flush()
'
}

exec > >(redact_stream) 2> >(redact_stream >&2)

EXEC="docker exec botji-hermes bash -c"

echo "=== CONTAINER RECENT LOGS (last 80 lines, timestamped) ==="
docker logs botji-hermes --tail 80 --timestamps 2>&1
echo ""

echo "=== VPS + DOCKER SNAPSHOT ==="
date -u +"utc_now=%Y-%m-%dT%H:%M:%SZ"
date +"local_now=%Y-%m-%dT%H:%M:%S%z"
docker inspect botji-hermes --format='name={{.Name}} image={{.Config.Image}} restart_count={{.RestartCount}} started={{.State.StartedAt}} running={{.State.Running}} status={{.State.Status}} oom_killed={{.State.OOMKilled}} exit_code={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || true
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps 2>/dev/null || docker ps --filter name=botji-hermes
echo ""

echo "=== RECENT DOCKER EVENTS FOR BOTJI (last 3h) ==="
docker events --since 3h --until "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" --filter container=botji-hermes 2>/dev/null | tail -80 || echo EVENTS_UNAVAILABLE
echo ""

echo "=== LAST DEPLOY MARKER ==="
$EXEC 'cat /opt/data/.botji/LAST_DEPLOY.json 2>/dev/null || echo LAST_DEPLOY_MISSING'
echo ""

echo "=== GATEWAY LOG — last 150 lines ==="
$EXEC 'tail -150 /opt/data/logs/gateway.log 2>/dev/null || echo GATEWAY_LOG_MISSING'
echo ""

echo "=== AGENT LOG — last 200 lines ==="
$EXEC 'tail -200 /opt/data/logs/agent.log 2>/dev/null || echo AGENT_LOG_MISSING'
echo ""

echo "=== GATEWAY LOG — last 2000 lines (extended window) ==="
$EXEC 'tail -2000 /opt/data/logs/gateway.log 2>/dev/null | grep -vE "memory_monitor|aiohttp.access.*GET /health" || echo NONE'
echo ""

echo "=== AGENT LOG — last 2000 lines (extended window, filtered) ==="
$EXEC 'tail -2000 /opt/data/logs/agent.log 2>/dev/null | grep -vE "memory_monitor|aiohttp.access.*GET /health|hermes_cli.plugins.*registered" || echo NONE'
echo ""

echo "=== INBOUND + RESPONSE TIMELINE (grep for slow requests) ==="
$EXEC 'grep -E "inbound message|response ready|conversation turn|API call|Turn ended|gate:|finalized|Suppressing" /opt/data/logs/gateway.log /opt/data/logs/agent.log 2>/dev/null | tail -80'
echo ""

echo "=== SESSION FILES CREATED IN LAST HOUR ==="
$EXEC 'find /opt/data/sessions -mmin -60 -type f 2>/dev/null | head -30'
echo ""

echo "=== ERRORS LOG — last 30 lines ==="
$EXEC 'tail -30 /opt/data/logs/errors.log 2>/dev/null || echo ERRORS_LOG_MISSING'
echo ""

echo "=== ARTIFACT INDEX — model/route/timing per artifact ==="
docker exec -i botji-hermes python3 - << 'PY'
import json, os
keys = ["artifact_id","role","adapter","created_at","model","chat_model","provider","route","contract_id","quality","size_bytes","parents"]
f = "/opt/data/artifacts/index/artifacts.jsonl"
if os.path.exists(f):
    for line in open(f):
        line = line.strip()
        if not line: continue
        try:
            r = json.loads(line)
            out = {k: r.get(k) for k in keys if r.get(k) is not None}
            print(json.dumps(out, default=str))
        except Exception:
            pass
else:
    print("INDEX_MISSING")
PY
echo ""

echo "=== LATEST 5 EVIDENCE FILES (extractor, summary, timing) ==="
$EXEC 'find /opt/data/artifacts/evidence -name "*.json" -printf "%T@ %p\n" 2>/dev/null | sort -n | tail -5 | cut -d" " -f2-' | while read -r f; do
    echo "--- $f ---"
    $EXEC "python3 -c \"
import json, sys
d = json.load(open('$f'))
print('extractor:', d.get('extractor'))
print('claim_level:', d.get('claim_level'))
print('created_at:', d.get('created_at'))
print('summary:', str(d.get('summary',''))[:200])
\""
    echo ""
done
echo ""

echo "=== LATEST 3 REVIEWS (verdict, blocking axes, conflicts) ==="
$EXEC 'find /opt/data/artifacts/reviews -name "*.json" -printf "%T@ %p\n" 2>/dev/null | sort -n | tail -3 | cut -d" " -f2-' | while read -r f; do
    echo "--- $f ---"
    $EXEC "python3 -c \"
import json, sys
d = json.load(open('$f'))
print('verdict:', d.get('verdict'))
print('contract_id:', d.get('contract_id'))
print('output_ref:', d.get('reviewed_output_ref'))
for ax in d.get('axes', []):
    sev = ax.get('severity','none')
    st  = ax.get('compare_status','')
    if sev in ('blocking','medium') or st in ('conflict','partial'):
        print('  AX', ax.get('axis'), st, sev)
        n = ax.get('notes','')
        try:
            nd = json.loads(n)
            for c in nd.get('conflicts',[]):
                print('    CONFLICT:', c[:150])
        except Exception:
            if n:
                print('    notes:', str(n)[:200])
\""
    echo ""
done
echo ""

echo "=== CONFIG: active model + image_gen ==="
$EXEC 'grep -E "default:|provider:|model:" /opt/data/config.yaml 2>/dev/null | head -15'
echo "--- env overrides ---"
$EXEC 'env | grep -E "BOTJI_IMAGE|BOTJI_VISION|BOTJI_CODEX" 2>/dev/null || echo none'
echo ""

echo "=== ENV: Telegram users + gateway mode (redacted) ==="
$EXEC 'echo TELEGRAM_ALLOWED_USERS_COUNT=$(echo "$TELEGRAM_ALLOWED_USERS" | tr "," "\n" | grep -c .); echo GATEWAY_ALLOW_ALL_USERS=$GATEWAY_ALLOW_ALL_USERS; echo TELEGRAM_BOT_USERNAME=$TELEGRAM_BOT_USERNAME'
echo ""

echo "=== CONTAINER STATUS ==="
docker inspect botji-hermes --format='Started: {{.State.StartedAt}}  RestartCount: {{.RestartCount}}  Health: {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}  OOMKilled: {{.State.OOMKilled}}  ExitCode: {{.State.ExitCode}}' 2>/dev/null
echo ""

echo "=== CODEX VERSION + AUTH STATE (redacted) ==="
$EXEC 'codex --version 2>&1; echo ---; if [ -f /opt/data/auth.json ]; then echo "auth.json present, size=$(stat -c%s /opt/data/auth.json) bytes, mtime=$(stat -c%y /opt/data/auth.json); contents redacted"; else echo "auth.json missing"; fi'
