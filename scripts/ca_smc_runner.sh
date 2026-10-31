#!/bin/bash
# resume-aware runner for scripts/ca_smc_fetch.py (San Mateo, port 9235,
# profile /tmp/claude-501/smc_profile). rc=2 = reCAPTCHA gate returned ->
# run the fully-local solver (ca_captcha_auto.py), then continue.
cd /Volumes/HulkBuster/Documents/Agents/maryland-property-agent || exit 1
FAILS=0
LOG=/tmp/ca_smc.log
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
start_chrome() {
  if ! curl -s http://localhost:9235/json/version >/dev/null 2>&1; then
    "$CHROME" --remote-debugging-port=9235 "--remote-allow-origins=*" \
      --user-data-dir=/tmp/claude-501/smc_profile \
      --no-first-run --no-default-browser-check about:blank >/tmp/smc9235_chrome.log 2>&1 &
    sleep 5
  fi
}
for i in $(seq 1 200); do
  start_chrome
  ./venv/bin/python scripts/ca_smc_fetch.py --years "${1:-2015-2026}" >> "$LOG" 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then echo "DONE rc=0 $(date)" >> "$LOG"; break; fi
  if [ $rc -eq 2 ]; then
    echo "CAPTCHA rc=2 -> local solver $(date)" >> "$LOG"
    ./venv/bin/python scripts/ca_captcha_auto.py 9235 apps.smcacre.org >> "$LOG" 2>&1
    src=$?
    sleep 3
    if [ $src -ne 0 ]; then
      FAILS=$((FAILS + 1))
      if [ $FAILS -ge 3 ]; then
        echo "solver failed x$FAILS -> wipe profile (risk-flagged) $(date)" >> "$LOG"
        pkill -f "remote-debugging-port=9235"; sleep 5
        rm -rf /tmp/claude-501/smc_profile
        FAILS=0
      fi
      BSECS=$((60 * FAILS)); [ $BSECS -gt 600 ] && BSECS=600
      echo "solver rc=$src fail#$FAILS -> backoff ${BSECS}s $(date)" >> "$LOG"
      pkill -f "remote-debugging-port=9235"; sleep $BSECS
    else
      FAILS=0
    fi
    continue
  fi
  echo "RESTART $i (rc=$rc) $(date)" >> "$LOG"
  pkill -f "remote-debugging-port=9235"; sleep 10
done
