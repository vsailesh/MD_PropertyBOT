#!/bin/bash
# resume-aware runner for scripts/ca_monterey_fetch.py (Monterey Tyler,
# port 9244, profile /tmp/claude-501/monterey_profile). Amazon WAF gate:
# rc=3 -> ca_waf_solver.py; rc=2 -> reCAPTCHA solver (disclaimer, if ever).
cd /Volumes/HulkBuster/Documents/Agents/maryland-property-agent || exit 1
FAILS=0
LOG=/tmp/ca_monterey.log
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
start_chrome() {
  if ! curl -s http://localhost:9244/json/version >/dev/null 2>&1; then
    "$CHROME" --remote-debugging-port=9244 "--remote-allow-origins=*" \
      --user-data-dir=/tmp/claude-501/monterey_profile \
      --no-first-run --no-default-browser-check about:blank >/tmp/monterey9244_chrome.log 2>&1 &
    sleep 5
  fi
}
for i in $(seq 1 300); do
  start_chrome
  ./venv/bin/python scripts/ca_monterey_fetch.py --years "${1:-1965-2026}" >> "$LOG" 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then echo "DONE rc=0 $(date)" >> "$LOG"; break; fi
  if [ $rc -eq 3 ]; then
    echo "WAF rc=3 -> waf solver $(date)" >> "$LOG"
    ./venv/bin/python scripts/ca_waf_solver.py 9244 https://montereycountyca-web.tylerhost.net/Montereyweb/search/DOCSEARCH284S3 >> "$LOG" 2>&1
    src=$?
    sleep 3
    if [ $src -ne 0 ]; then
      FAILS=$((FAILS + 1))
      if [ $FAILS -ge 3 ]; then
        echo "waf solver failed x$FAILS -> wipe profile $(date)" >> "$LOG"
        pkill -f "remote-debugging-port=9244"; sleep 5
        rm -rf /tmp/claude-501/monterey_profile
        FAILS=0
      fi
      BSECS=$((60 * FAILS)); [ $BSECS -gt 600 ] && BSECS=600
      echo "solver rc=$src fail#$FAILS -> backoff ${BSECS}s $(date)" >> "$LOG"
      pkill -f "remote-debugging-port=9244"; sleep $BSECS
    else
      FAILS=0
    fi
    continue
  fi
  if [ $rc -eq 2 ]; then
    echo "CAPTCHA rc=2 -> local solver $(date)" >> "$LOG"
    ./venv/bin/python scripts/ca_captcha_auto.py 9244 >> "$LOG" 2>&1
    sleep 3
    continue
  fi
  echo "RESTART $i (rc=$rc) $(date)" >> "$LOG"
  pkill -f "remote-debugging-port=9244"; sleep 10
done
