#!/bin/bash
# resume-aware runner for scripts/ca_oc_fetch.py — fetch_log makes restarts
# cheap. Restarts organic Chrome (port 9227) if CDP dies.
cd /Volumes/HulkBuster/Documents/Agents/maryland-property-agent || exit 1
LOG=/tmp/ca_oc.log
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
start_chrome() {
  if ! curl -s http://localhost:9227/json/version >/dev/null 2>&1; then
    "$CHROME" --remote-debugging-port=9227 "--remote-allow-origins=*" \
      --user-data-dir=/tmp/claude-501/oc_profile \
      --no-first-run --no-default-browser-check about:blank >/tmp/oc9227_chrome.log 2>&1 &
    sleep 5
  fi
}
for i in $(seq 1 200); do
  start_chrome
  ./venv/bin/python scripts/ca_oc_fetch.py --years "${1:-2010-2026}" >> "$LOG" 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then echo "DONE rc=0 $(date)" >> "$LOG"; break; fi
  echo "RESTART $i (rc=$rc) $(date)" >> "$LOG"
  pkill -f "remote-debugging-port=9227"; sleep 10
done
