#!/bin/bash
# SDAT watchdog: keep a scrape batch running through intermittent Cloudflare
# blocks. Probes nothing itself — `robust_bulk_search run` already gates on a
# 3-probe check and aborts cleanly when blocked; streets stay pending. This
# loop just retries every 10 minutes until the pending queue drains.
#
# Any args pass through to the run command, e.g.:
#   nohup ./scripts/sdat_watchdog.sh > /dev/null 2>&1 &              # resume oldest batch
#   ./scripts/sdat_watchdog.sh -i data/refresh_rotation.xlsx --force --replace
cd "$(dirname "$0")/.." || exit 1
LOG=logs/sdat_watchdog.log
mkdir -p logs

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
    if [ "$FIRST" -eq 1 ]; then
        ./venv/bin/python scripts/robust_bulk_search.py run --no-filter "$@" >> "$LOG" 2>&1
        FIRST=0
    else
        ./venv/bin/python scripts/robust_bulk_search.py run --no-filter >> "$LOG" 2>&1
    fi

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
        echo "$(date '+%F %T') blocked or interrupted — $PENDING streets pending, retry $(( WAIT / 60 )) min (no-progress streak $STREAK)" >> "$LOG"
        sleep "$WAIT"
    else
        echo "$(date '+%F %T') no pending streets — watchdog done" >> "$LOG"
        break
    fi
done
