#!/bin/bash
# resume-aware runner for scripts/ca_vc_fetch.py (Ventura, port 9226,
# profile /tmp/claude-501/oc_recon_profile). rc=2 means the Tyler
# reCAPTCHA gate needs a manual/vision solve in the running chrome —
# don't restart-loop, just stop and log.
cd /Volumes/HulkBuster/Documents/Agents/maryland-property-agent || exit 1
LOG=/tmp/ca_vc.log
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
start_chrome() {
  if ! curl -s http://localhost:9226/json/version >/dev/null 2>&1; then
    "$CHROME" --remote-debugging-port=9226 "--remote-allow-origins=*" \
      --user-data-dir=/tmp/claude-501/oc_recon_profile \
      --no-first-run --no-default-browser-check about:blank >/tmp/oc9226_chrome.log 2>&1 &
    sleep 5
  fi
}
for i in $(seq 1 200); do
  start_chrome
  ./venv/bin/python scripts/ca_vc_fetch.py --years "${1:-2015-2026}" >> "$LOG" 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then echo "DONE rc=0 $(date)" >> "$LOG"; break; fi
  if [ $rc -eq 2 ]; then echo "CAPTCHA-NEEDS-SOLVE $(date)" >> "$LOG"; break; fi
  echo "RESTART $i (rc=$rc) $(date)" >> "$LOG"
  pkill -f "remote-debugging-port=9226"; sleep 10
done
