#!/bin/bash
# Refresh rotation entry point (launchd target). Rotates the N stalest
# streets through a replace re-scrape and drains the queue through any
# Cloudflare blocks via the watchdog.
#
# launchd fires this DAILY; the script self-gates to ~weekly via a success
# marker. A single Sunday-only StartCalendarInterval window is skipped
# forever if the Mac is off at that moment (launchd never re-runs missed
# calendar fires); daily probing lets it catch up instead.
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
MARKER=logs/.last_refresh_rotation
NOW=$(date +%s)
LAST=$(cat "$MARKER" 2>/dev/null || echo 0)
AGE=$(( NOW - LAST ))
# 6 days (not 7): daily probe then lands on day 7, not day 8+ drift
if [ "$AGE" -lt $(( 6 * 86400 )) ]; then exit 0; fi
# Don't rotate while the drain queue is non-empty (2026-10: batches
# 193/194 hold ~26k streets for weeks). The always-on watchdog owns the
# queue; a rotation now would start a second watchdog (lock-exits 3,
# marker never updates) and re-target the same 2k stalest streets daily.
PENDING=$(sqlite3 data/property_search.db "
    SELECT COUNT(*) FROM search_progress sp
    JOIN batches b ON sp.batch_id = b.id
    WHERE b.status IN ('in_progress', 'interrupted') AND sp.status = 'pending'")
if [ "${PENDING:-0}" -gt 0 ] 2>/dev/null; then
    echo "=== $(date '+%F %T') rotation deferred — $PENDING streets still draining ===" >> logs/weekly_refresh.log
    exit 0
fi
echo "=== $(date '+%F %T') refresh rotation (last success ${AGE}s ago) ===" >> logs/weekly_refresh.log
if ./venv/bin/python scripts/refresh_rotation.py --limit 2000 --run >> logs/weekly_refresh.log 2>&1; then
    echo "$NOW" > "$MARKER"
    echo "=== $(date '+%F %T') refresh rotation done ===" >> logs/weekly_refresh.log
else
    echo "=== $(date '+%F %T') refresh rotation FAILED (marker not updated; retried next probe) ===" >> logs/weekly_refresh.log
fi
