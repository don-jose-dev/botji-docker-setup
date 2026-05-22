#!/usr/bin/env bash
# botji-nightly-regression.sh — invoked by the Hermes cron scheduler at
# 03:30 nightly under no_agent=True. The scheduler captures stdout
# verbatim; empty stdout means "silent tick, nothing to report".
#
# Semantics (matched to scheduler.py no_agent block at line 1152):
#   exit 0 + empty stdout  → silent run, no delivery, success=True
#   exit 0 + non-empty     → stdout delivered as message
#   non-zero exit / timeout → scheduler delivers a watchdog-broken alert
#
# We exploit "empty stdout = silent" for the happy path: if every suite
# passes we say nothing. Failures print a short summary so the
# scheduler's local output file becomes the triage artefact.

set -u  # NB: NO -e — we want to capture the harness exit code, not
        # abort on a non-zero suite, so we can write the failure log.

# Persistent logs (separate from the scheduler's per-run output dir).
# These survive across runs and are tailed by humans during triage.
LOG_DIR="${BOTJI_HARNESS_LOG_DIR:-/opt/data/logs/botji-harness}"
mkdir -p "$LOG_DIR" 2>/dev/null || true

TS="$(date -u +"%Y%m%dT%H%M%SZ")"
LOG_FILE="$LOG_DIR/nightly-$TS.log"

if ! command -v botji-harness >/dev/null 2>&1; then
  # PATH missing botji-harness — surface this clearly. Non-empty stdout
  # makes the scheduler deliver it as a failure alert.
  echo "ERROR: botji-harness not found on PATH. PR #26 should have"
  echo "       installed it; check Dockerfile and runtime/bin/."
  exit 2
fi

# Run the harness, capturing combined stdout+stderr both to a variable
# (for the fail-path summary) and to the persistent log (for triage).
# We can't pipe to `tee` and use $?, since in a pipeline $? reflects
# the LAST command. Capture first, then write.
HARNESS_OUTPUT="$(botji-harness run --suite all 2>&1)"
HARNESS_RC=$?

{
  echo "# botji-nightly-regression $TS  rc=$HARNESS_RC"
  echo "# command: botji-harness run --suite all"
  echo "---"
  printf "%s\n" "$HARNESS_OUTPUT"
} >> "$LOG_FILE" 2>/dev/null || true

if [ "$HARNESS_RC" -eq 0 ]; then
  # Happy path: print nothing, exit 0. Scheduler treats this as a
  # silent tick — no delivery, but the persistent log is kept.
  exit 0
fi

# Failure: print a triage summary. Scheduler delivers this verbatim
# to deliver=local (~/.hermes/cron/output/{job_id}/).
echo "FAIL: botji-harness regression failed (rc=$HARNESS_RC)"
echo "      log: $LOG_FILE"
echo "---"
# Tail the last ~40 lines of harness output so the summary message
# carries enough context to triage without opening the log file.
printf "%s\n" "$HARNESS_OUTPUT" | tail -n 40
exit 1
