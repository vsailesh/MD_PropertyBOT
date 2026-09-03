#!/bin/bash
# Weekly refresh entry point (launchd target). Rotates the N stalest streets
# through a replace re-scrape and drains the queue through any Cloudflare
# blocks via the watchdog.
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
echo "=== $(date '+%F %T') weekly refresh ===" >> logs/weekly_refresh.log
./venv/bin/python scripts/refresh_rotation.py --limit 2000 --run >> logs/weekly_refresh.log 2>&1
echo "=== $(date '+%F %T') weekly refresh done ===" >> logs/weekly_refresh.log
