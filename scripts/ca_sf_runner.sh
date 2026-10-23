#!/bin/bash
# resume-aware runner for scripts/ca_sf_fetch.py (SF pure-API walker).
cd /Volumes/HulkBuster/Documents/Agents/maryland-property-agent || exit 1
LOG=/tmp/ca_sf.log
for i in $(seq 1 200); do
  ./venv/bin/python scripts/ca_sf_fetch.py --years "${1:-1990-2026}" >> "$LOG" 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then echo "DONE rc=0 $(date)" >> "$LOG"; break; fi
  echo "RESTART $i (rc=$rc) $(date)" >> "$LOG"
  sleep 20
done
