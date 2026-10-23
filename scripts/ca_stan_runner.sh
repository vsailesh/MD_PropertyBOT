#!/bin/bash
# resume-aware runner for scripts/ca_stan_fetch.py (Stanislaus, port 9232,
# profile /tmp/claude-501/stan_profile). rc=2 = reCAPTCHA gate returned ->
# run the fully-local solver (ca_captcha_auto.py), then continue.
cd /Volumes/HulkBuster/Documents/Agents/maryland-property-agent || exit 1
LOG=/tmp/ca_stan.log
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
start_chrome() {
  if ! curl -s http://localhost:9232/json/version >/dev/null 2>&1; then
    "$CHROME" --remote-debugging-port=9232 "--remote-allow-origins=*" \
      --user-data-dir=/tmp/claude-501/stan_profile \
      --no-first-run --no-default-browser-check about:blank >/tmp/stan9232_chrome.log 2>&1 &
    sleep 5
  fi
}
for i in $(seq 1 200); do
  start_chrome
  ./venv/bin/python scripts/ca_stan_fetch.py --years "${1:-2015-2026}" >> "$LOG" 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then echo "DONE rc=0 $(date)" >> "$LOG"; break; fi
  if [ $rc -eq 2 ]; then
    echo "CAPTCHA rc=2 -> local solver $(date)" >> "$LOG"
    ./venv/bin/python scripts/ca_captcha_auto.py 9232 >> "$LOG" 2>&1
    src=$?
    sleep 3
    if [ $src -ne 0 ]; then
      echo "solver rc=$src -> fresh chrome $(date)" >> "$LOG"
      pkill -f "remote-debugging-port=9232"; sleep 8
    fi
    continue
  fi
  echo "RESTART $i (rc=$rc) $(date)" >> "$LOG"
  pkill -f "remote-debugging-port=9232"; sleep 10
done
