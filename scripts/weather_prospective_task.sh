#!/usr/bin/env bash
# Linux equivalent of scripts/weather_prospective_task.ps1 (run by systemd).
#
# - Uses only this repo's .venv interpreter (refuses any other).
# - flock on data/logs/prospective/cycle.flock: the kernel releases it when the
#   process dies, so there are no stale locks; a second invocation exits.
# - Ownership marker is fetched fresh before every cycle. If it cannot be
#   verified or names another collector, the cycle is skipped and an alert is
#   sent. A missing collector config is also a skip: there is no standalone mode.
# - Logs to data/logs/prospective/YYYY-MM-DD.log (UTC), same format as Windows.
# - Retries the cycle once on a non-zero exit (captures are idempotent).
# - After the cycle: collector health ping, then state backup with its own
#   health ping. Backup failures never change the cycle exit code.
set -uo pipefail

RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-90}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$REPO_ROOT/data/logs/prospective"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date -u +%Y-%m-%d).log"

log() {
    printf '%s [pid %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)" "$$" "$1" >> "$LOG"
}

run_logged() {
    # run_logged <args...>: run the repo interpreter, append its output to the log, return its exit code.
    local out
    out="$(mktemp "$LOG_DIR/.cycle_XXXXXX.out")"
    "$PYTHON" "$@" > "$out" 2>&1
    local rc=$?
    while IFS= read -r line || [[ -n "$line" ]]; do log "  $line"; done < "$out"
    rm -f "$out"
    return $rc
}

exec 9>>"$LOG_DIR/cycle.flock"
if ! flock -n 9; then
    log "another cycle is running; exiting without work"
    exit 0
fi

cd "$REPO_ROOT" || exit 12
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 COLUMNS=200
export PYTHONTZPATH=""            # zoneinfo from the pinned pip tzdata, as on Windows
export KEL_CYCLE_LOCK_HELD=1
export KEL_COLLECTOR_CONFIG="${KEL_COLLECTOR_CONFIG:-/etc/kalshi-edge-lab/collector.json}"

log "cycle start; repo=$REPO_ROOT; host=$(hostname); config=$KEL_COLLECTOR_CONFIG"
if [[ ! -x "$PYTHON" ]]; then
    log "ERROR: repo venv interpreter missing: $PYTHON"
    exit 10
fi
prefix="$("$PYTHON" -c 'import sys; print(sys.prefix)')"
if [[ "$(realpath "$prefix")" != "$(realpath "$REPO_ROOT/.venv")" ]]; then
    log "ERROR: interpreter sys.prefix '$prefix' is not the repo venv"
    exit 11
fi
et_now="$("$PYTHON" -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('America/New_York')).isoformat())")"
log "venv ok ($prefix); America/New_York now $et_now"

run_logged -m research.weather.collector_ops guard --require-config
guard_rc=$?
if [[ $guard_rc -ne 0 ]]; then
    log "ownership check exit $guard_rc; cycle skipped (no capture, no backfill)"
    log "cycle end (exit $guard_rc)"
    exit $guard_rc
fi

run_logged weather_model.py --prospective-cycle
exit_code=$?
log "cycle exit code $exit_code"
if [[ $exit_code -ne 0 ]]; then
    log "retry 1 in $RETRY_DELAY_SECONDS s (idempotent; existing captures are never overwritten)"
    sleep "$RETRY_DELAY_SECONDS"
    run_logged weather_model.py --prospective-cycle
    exit_code=$?
    log "retry 1 exit code $exit_code"
fi

run_logged -m research.weather.collector_ops post --cycle-exit "$exit_code"
log "cycle end (exit $exit_code)"
exit "$exit_code"
