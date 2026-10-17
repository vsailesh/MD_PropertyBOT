#!/usr/bin/env python3
"""Queue the universe streets that are NOT covered and NOT already pending.

Gap decomposition (2026-10-17, norm keys per coverage_report):
  covered 58,374 | pending-only 18,159 | zero-completed 1,063 | never 29

This queues the last two as new batches (FIFO behind 193/194 — the
watchdog drains in batch order, so nothing is preempted):
  - Never_Tried_<date>: 29 streets (mostly ST-prefixed bulk spellings the
    TIGER input list never carried; engine variant layers handle those)
  - Straggler_Sweep_<date>: zero-completed in-universe streets. The name
    activates the engine's dormant straggler layer (prefix-drop search +
    canonical row filter, sdat_browser_scraper.py) — see memory
    coverage-honesty-phantoms.

Raw spellings come from the bulk parcel store (the universe), same as the
phantom rescue. Provenance: data/gap_fill_<date>.json

Usage: python scripts/queue_missing_streets.py [--dry-run]
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from coverage_report import norm_street, norm_county, MAIN_DB, BULK_DB


def decompose():
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    con.execute(f"ATTACH DATABASE 'file:{BULK_DB}?mode=ro' AS bulk", )
    cur = con.cursor()

    # universe keys -> raw bulk spellings (first spelling wins)
    raw = {}
    for street, county in cur.execute(
            "SELECT DISTINCT street, county FROM bulk.parcels "
            "WHERE street IS NOT NULL AND street != ''"):
        k = (norm_street(street), norm_county(county))
        if k[0] and k not in raw:
            raw[k] = (street, county)

    have_rows = set()
    for street, county in cur.execute(
            "SELECT DISTINCT street_name, county FROM properties"):
        k = (norm_street(street), norm_county(county))
        if k[0]:
            have_rows.add(k)

    sp_status = {}
    for street, county, status in cur.execute(
            "SELECT DISTINCT street_name, county, status FROM search_progress"):
        k = (norm_street(street), norm_county(county))
        if k[0]:
            sp_status.setdefault(k, set()).add(status)

    covered = {k for k in raw if k in have_rows}
    any_sp = {k for k in raw if k in sp_status}
    pending = {k for k in any_sp if "pending" in sp_status[k]} - covered
    zero = {k for k in any_sp if "completed" in sp_status[k]} - covered - pending
    never = (set(raw) - any_sp) - covered
    con.close()
    return raw, covered, pending, zero, never


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw, covered, pending, zero, never = decompose()
    print(f"covered {len(covered):,} | pending {len(pending):,} | "
          f"zero {len(zero):,} | never {len(never):,}")

    never_streets = sorted(raw[k] for k in never)
    zero_streets = sorted(raw[k] for k in zero)

    prov = {
        "date": date.today().isoformat(),
        "never_tried": [{"street": s, "county": c} for s, c in never_streets],
        "zero_completed": [{"street": s, "county": c} for s, c in zero_streets],
    }

    if args.dry_run:
        print("never sample:", never_streets[:8])
        print("zero sample:", zero_streets[:8])
        return

    from src.robust_database import PropertyDatabase, SearchJobManager
    db = PropertyDatabase(os.path.join(ROOT, "data", "property_search.db"))
    jm = SearchJobManager(db)

    if never_streets:
        bid = jm.create_search_job(never_streets, f"Never_Tried_{date.today():%Y%m%d}")
        prov["never_batch_id"] = bid
        print(f"Never_Tried batch {bid}: {len(never_streets)} streets")
    if zero_streets:
        # force=True: zero streets are status='completed' — without force the
        # completed filter would drop every one of them
        bid = jm.create_search_job(zero_streets, f"Straggler_Sweep_{date.today():%Y%m%d}",
                                   force=True)
        prov["straggler_batch_id"] = bid
        print(f"Straggler_Sweep batch {bid}: {len(zero_streets)} streets")

    path = os.path.join(ROOT, "data", f"gap_fill_{date.today():%Y%m%d}.json")
    with open(path, "w") as f:
        json.dump(prov, f, indent=1)
    print(f"provenance: {path}")


if __name__ == "__main__":
    main()
