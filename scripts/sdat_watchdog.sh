#!/bin/bash
# SDAT watchdog: keep a scrape batch running through intermittent Cloudflare
# blocks. Probes nothing itself — `robust_bulk_search run` already gates on a
# 3-probe check and aborts cleanly when blocked; streets stay pending. This
# loop just retries until the pending queue drains.
#
# Night window: passes only START inside 01:00-06:00 (low SDAT traffic), and
# each pass is killed at window end — the batch resumes next night. SQLite
# state is crash-proof, so the interrupt is safe. Override for manual runs:
#   SDAT_ANYTIME=1 ./scripts/sdat_watchdog.sh ...
#
# Any args pass through to the run command, e.g.:
#   nohup ./scripts/sdat_watchdog.sh > /dev/null 2>&1 &              # resume oldest batch
#   ./scripts/sdat_watchdog.sh -i data/refresh_rotation.xlsx --force --replace
cd "$(dirname "$0")/.." || exit 1
LOG=logs/sdat_watchdog.log
mkdir -p logs

WIN_START=60    # 01:00 in minutes-of-day
WIN_END=360     # 06:00

# Single-watchdog guard: a second instance (launchd weekly refresh firing
# while a manual drain is still running) would double the request rate and
# aggravate the Cloudflare WAF. Busy → exit, the running one owns the queue.
LOCK=/tmp/sdat_watchdog.lock
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
    echo "$(date '+%F %T') another watchdog already running (pid $(cat "$LOCK")) — exiting" >> "$LOG"
    exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

echo "=== $(date '+%F %T') watchdog start (args: $*) ===" >> "$LOG"

# Echo seconds until the night window opens (0 if inside it)
secs_to_window() {
    local M=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
    if [ "$M" -ge "$WIN_START" ] && [ "$M" -lt "$WIN_END" ]; then
        echo 0
    elif [ "$M" -lt "$WIN_START" ]; then
        echo $(( (WIN_START - M) * 60 ))
    else
        echo $(( (1440 - M + WIN_START) * 60 ))
    fi
}

# Echo seconds remaining in the night window (0 if outside)
window_left() {
    local M=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
    if [ "$M" -lt "$WIN_END" ]; then
        echo $(( (WIN_END - M) * 60 ))
    else
        echo 0
    fi
}

# First pass honors the args (e.g. `-i FILE --force --replace` to create the
# job). Later passes resume with no args — re-running with `-i --force` would
# re-queue the same streets forever.
FIRST=1
# Exponential backoff across consecutive no-progress retries: retrying a
# hard Cloudflare block every 10 min just keeps the WAF warm. 10min → 30min
# → 1h → 3h → 6h cap; resets whenever the pending count actually drops.
STREAK=0
LAST_PENDING=""
while true; do
    # Night-window gate (unless overridden for manual runs)
    if [ -z "$SDAT_ANYTIME" ]; then
        TO_WIN=$(secs_to_window)
        if [ "$TO_WIN" -gt 0 ]; then
            echo "$(date '+%F %T') outside 01:00-06:00 window — sleeping $(( TO_WIN / 60 )) min" >> "$LOG"
            # backoff WAIT may not exceed time-to-window; re-checked each wake
            sleep "$TO_WIN"
        fi
    fi

    LEFT=$(window_left)
    if [ "$FIRST" -eq 1 ]; then
        ARGS="$*"
    else
        ARGS=""
    fi
    # Kill the pass at window end (crash-proof resume picks up next night)
    ./venv/bin/python scripts/robust_bulk_search.py run --no-filter $ARGS >> "$LOG" 2>&1 &
    RUN_PID=$!
    if [ -z "$SDAT_ANYTIME" ] && [ "$LEFT" -gt 0 ]; then
        ( sleep "$LEFT"; kill "$RUN_PID" 2>/dev/null ) &
        TIMER_PID=$!
    else
        TIMER_PID=""
    fi
    wait "$RUN_PID" 2>/dev/null
    [ -n "$TIMER_PID" ] && kill "$TIMER_PID" 2>/dev/null
    FIRST=0

    PENDING=$(sqlite3 data/property_search.db "
        SELECT COUNT(*) FROM search_progress sp
        JOIN batches b ON sp.batch_id = b.id
        WHERE b.status = 'in_progress' AND sp.status = 'pending'")

    if [ "$PENDING" -gt 0 ] 2>/dev/null; then
        if [ -n "$LAST_PENDING" ] && [ "$PENDING" -lt "$LAST_PENDING" ]; then
            STREAK=0   # progress happened — fresh block tolerance
        else
            STREAK=$(( STREAK + 1 ))
        fi
        LAST_PENDING=$PENDING
        case "$STREAK" in
            0|1) WAIT=600 ;;
            2)   WAIT=1800 ;;
            3)   WAIT=3600 ;;
            4)   WAIT=10800 ;;
            *)   WAIT=21600 ;;
        esac
        # Don't sleep past the window opening — cap wait at time-to-window
        if [ -z "$SDAT_ANYTIME" ]; then
            TO_WIN=$(secs_to_window)
            [ "$TO_WIN" -gt 0 ] && [ "$TO_WIN" -lt "$WAIT" ] && WAIT=$TO_WIN
        fi
        echo "$(date '+%F %T') blocked or interrupted — $PENDING streets pending, retry $(( WAIT / 60 )) min (no-progress streak $STREAK)" >> "$LOG"
        sleep "$WAIT"
    else
        echo "$(date '+%F %T') no pending streets — watchdog done" >> "$LOG"
        break
    fi
done
