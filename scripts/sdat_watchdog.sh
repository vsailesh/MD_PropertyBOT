#!/bin/bash
# SDAT watchdog: keep a scrape batch running through intermittent Cloudflare
# blocks. Probes nothing itself — `robust_bulk_search run` already gates on a
# 3-probe check and aborts cleanly when blocked; streets stay pending. This
# loop just retries every 10 minutes until the pending queue drains.
#
# Usage: nohup ./scripts/sdat_watchdog.sh > /dev/null 2>&1 &
cd "$(dirname "$0")/.." || exit 1
LOG=logs/sdat_watchdog.log
mkdir -p logs
echo "=== $(date '+%F %T') watchdog start ===" >> "$LOG"

while true; do
    ./venv/bin/python scripts/robust_bulk_search.py run --no-filter >> "$LOG" 2>&1

    PENDING=$(sqlite3 data/property_search.db "
        SELECT COUNT(*) FROM search_progress sp
        JOIN batches b ON sp.batch_id = b.id
        WHERE b.status = 'in_progress' AND sp.status = 'pending'")

    if [ "$PENDING" -gt 0 ] 2>/dev/null; then
        echo "$(date '+%F %T') blocked or interrupted — $PENDING streets pending, retry in 10 min" >> "$LOG"
        sleep 600
    else
        echo "$(date '+%F %T') no pending streets — watchdog done" >> "$LOG"
        break
    fi
done
