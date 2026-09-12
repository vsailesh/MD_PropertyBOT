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
echo "=== $(date '+%F %T') refresh rotation (last success ${AGE}s ago) ===" >> logs/weekly_refresh.log
if ./venv/bin/python scripts/refresh_rotation.py --limit 2000 --run >> logs/weekly_refresh.log 2>&1; then
    echo "$NOW" > "$MARKER"
    echo "=== $(date '+%F %T') refresh rotation done ===" >> logs/weekly_refresh.log
else
    echo "=== $(date '+%F %T') refresh rotation FAILED (marker not updated; retried next probe) ===" >> logs/weekly_refresh.log
fi
