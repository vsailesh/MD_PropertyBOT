#!/bin/bash
# resume-aware runner for scripts/nj_taxdatahub_fetch.py — the fetch log
# makes restarts cheap (logged (county,query) pairs are skipped).
# /tmp copy vanished in a tmp cleanup once; keep this one in the repo.
cd /Volumes/HulkBuster/Documents/Agents/maryland-property-agent || exit 1
LOG=/tmp/nj_tdh_fetch3.log
for i in $(seq 1 60); do
  ./venv/bin/python scripts/nj_taxdatahub_fetch.py --counties SOMERSET,CAMDEN,ATLANTIC >> "$LOG" 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then echo "DONE rc=0 $(date)" >> "$LOG"; break; fi
  echo "RESTART $i (rc=$rc) $(date)" >> "$LOG"
  sleep 20
done
