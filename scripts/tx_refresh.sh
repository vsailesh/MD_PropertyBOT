#!/bin/bash
# TX weekly refresh (launchd target, daily probe + 6-day gate like
# weekly_refresh.sh — never Sunday-only, launchd skips missed windows).
# Full re-walk: tx_fetch --refresh upserts parcels (ownership moves
# slowly; ~2h), reclassify for any dict drift, geocode new situs, export.
# Logs to ~/Library/Logs (NOT repo logs — external-volume launchd
# stdout redirect dies with EX_CONFIG; see launchd-external-volume
# memory).
cd "$(dirname "$0")/.." || exit 1
# Never stack a refresh on a live fetch/geocode — two writers on the same
# sqlite file (the initial 44k-row geocode backlog ran ~8h past the first
# fire window). Retry next daily probe instead.
if pgrep -f "scripts/tx_(fetch|geocode|export|reclassify).py" > /dev/null; then
    echo "=== $(date '+%F %T') tx refresh deferred — fetch/geocode already running ===" >> "$HOME/Library/Logs/tx-property-refresh.log"
    exit 0
fi
MARKER=logs/.last_tx_refresh
NOW=$(date +%s)
LAST=$(cat "$MARKER" 2>/dev/null || echo 0)
if [ $(( NOW - LAST )) -lt $(( 6 * 86400 )) ]; then exit 0; fi
echo "=== $(date '+%F %T') tx refresh (last ${LAST}) ===" >> "$HOME/Library/Logs/tx-property-refresh.log"
if ./venv/bin/python scripts/tx_fetch.py --refresh --threads 4 >> "$HOME/Library/Logs/tx-property-refresh.log" 2>&1 \
   && ./venv/bin/python scripts/tx_reclassify.py >> "$HOME/Library/Logs/tx-property-refresh.log" 2>&1; then
    while true; do
        out=$(./venv/bin/python scripts/tx_geocode.py --limit 2000 2>&1 | tail -1)
        echo "$out" >> "$HOME/Library/Logs/tx-property-refresh.log"
        case "$out" in *"nothing to do"*) break;; esac
        case "$out" in geocoded\ 0/*) break;; esac
    done
    ./venv/bin/python scripts/tx_export.py >> "$HOME/Library/Logs/tx-property-refresh.log" 2>&1
    echo "$NOW" > "$MARKER"
    echo "=== $(date '+%F %T') tx refresh done ===" >> "$HOME/Library/Logs/tx-property-refresh.log"
else
    echo "=== $(date '+%F %T') tx refresh FAILED (marker not updated) ===" >> "$HOME/Library/Logs/tx-property-refresh.log"
fi
