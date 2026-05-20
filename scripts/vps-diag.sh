#!/usr/bin/env bash
# Runs ON the VPS via SSH. Focused diagnostic: message timing, steps, model used.
set -euo pipefail

EXEC="docker exec botji-hermes bash -c"

echo "=== ALL VPS CONTAINERS (docker ps -a) ==="
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>&1
echo ""

echo "=== SYSTEMD UNITS (telegram/bot/agent related) ==="
systemctl list-units --type=service --all 2>/dev/null | grep -iE "bot|telegram|hermes|agent|degain" | head -20 || echo "no systemctl access"
echo ""

echo "=== CONTAINER RECENT LOGS (last 80 lines, timestamped) ==="
docker logs botji-hermes --tail 80 --timestamps 2>&1
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
docker inspect botji-hermes --format='Started: {{.State.StartedAt}}  Health: {{.State.Health.Status}}' 2>/dev/null
echo ""

echo "=== CODEX VERSION + AUTH STATE (redacted) ==="
$EXEC 'codex --version 2>&1; echo ---; if [ -f /opt/data/auth.json ]; then echo "auth.json present, size=$(stat -c%s /opt/data/auth.json) bytes, mtime=$(stat -c%y /opt/data/auth.json); contents redacted"; else echo "auth.json missing"; fi'
