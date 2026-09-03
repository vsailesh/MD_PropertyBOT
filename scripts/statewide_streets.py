#!/usr/bin/env python3
"""
Statewide street diff: MDP bulk parcels vs streets we've ever searched.

The bulk dataset (data/md_bulk.db, from Socrata ed4q-f8tm) is the canonical
street list for Maryland — every street that has at least one tax parcel.
Our search_progress table is what we've actually queued. The difference is
streets that have never been searched: no owner names for them, ever.

Outputs an Excel (Street/County) usable directly as
`robust_bulk_search.py run -i FILE` input. With --create-job, queues the
batch immediately (batch drains after older in-progress batches — the
`run` command picks the oldest in_progress batch first).

Usage:
  python scripts/statewide_streets.py                     # stats + Excel
  python scripts/statewide_streets.py --create-job        # also queue batch
  python scripts/statewide_streets.py --min-parcels 3     # skip 1-2 parcel stubs
"""
import argparse
import os
import sqlite3
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
MAIN_DB = os.path.join(DATA_DIR, "property_search.db")
BULK_DB = os.path.join(DATA_DIR, "md_bulk.db")
sys.path.insert(0, ROOT)


def norm_county(name: str) -> str:
    """Match coverage_audit keying: upper, strip ' COUNTY', keep ' CITY'."""
    c = str(name).upper().strip()
    if c.endswith(" CITY"):
        return c
    if c.endswith(" COUNTY"):
        c = c[: -len(" COUNTY")].strip()
    return c


def display_county(bulk_county: str) -> str:
    """Bulk county ('BALTIMORE CITY'/'HOWARD') → county_map-friendly display."""
    return bulk_county if bulk_county.endswith(" CITY") else bulk_county.title() + " County"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-parcels", type=int, default=1,
                    help="only queue streets with at least N parcels (default 1)")
    ap.add_argument("--output", default=os.path.join(DATA_DIR, "statewide_missing.xlsx"))
    ap.add_argument("--create-job", action="store_true",
                    help="queue the batch now (drains after older batches)")
    args = ap.parse_args()

    main_db = sqlite3.connect(MAIN_DB)
    main_db.execute("ATTACH DATABASE ? AS bulk", (BULK_DB,))

    bulk = pd.read_sql_query(
        """SELECT county, street, COUNT(*) n_parcels
           FROM bulk.parcels
           WHERE street != '' AND hnum IS NOT NULL
           GROUP BY county, street""",
        main_db,
    )
    searched = pd.read_sql_query(
        "SELECT street_name, county FROM search_progress", main_db
    )
    # properties.street_name is the other half of "what we've searched" —
    # scraped streets exist there even when their progress row used a variant
    # format ('10TH -11TH' vs '10TH'). Normalize both through the cleaner so
    # variants collapse to one key.
    prop_streets = pd.read_sql_query(
        "SELECT DISTINCT street_name, county FROM properties", main_db
    )
    main_db.close()

    from src.street_name_cleaner import StreetNameCleaner
    cleaner = StreetNameCleaner()

    def key(street, county):
        cleaned = cleaner.clean_street_name(str(street))
        cleaned = str(cleaned).upper().strip() if cleaned else str(street).upper().strip()
        return (cleaned, norm_county(county))

    searched_keys = set(
        key(s, c) for s, c in zip(searched.street_name, searched.county)
    ) | set(
        key(s, c) for s, c in zip(prop_streets.street_name, prop_streets.county)
    )

    never = bulk[
        ~bulk.apply(lambda r: (r.street, norm_county(r.county)) in searched_keys, axis=1)
    ].copy()
    # bulk streets are cleaner-normalized already (same cleaner at download)

    total_bulk = len(bulk)
    print(f"bulk street keys      : {total_bulk:,}")
    print(f"searched street keys  : {len(searched_keys):,}")
    print(f"never searched        : {len(never):,}")

    never = never[never.n_parcels >= args.min_parcels].sort_values(
        "n_parcels", ascending=False)
    print(f"after --min-parcels {args.min_parcels}: {len(never):,}")

    out = pd.DataFrame({
        "Street": never.street,
        "County": never.county.map(display_county),
    })
    out.to_excel(args.output, index=False)
    print(f"✅ wrote {len(out):,} streets to {args.output}")
    print(f"   parcels behind them: {int(never.n_parcels.sum()):,}")

    if args.create_job:
        from src.robust_database import PropertyDatabase, SearchJobManager
        db = PropertyDatabase(os.path.join(ROOT, "data", "property_search.db"))
        mgr = SearchJobManager(db)
        streets = list(zip(out.Street, out.County))
        batch_id = mgr.create_search_job(
            streets, job_name=f"Statewide_Never_Searched_{args.min_parcels}p")
        print(f"✅ queued batch {batch_id} — `run` picks oldest in_progress batch first")


if __name__ == "__main__":
    sys.exit(main())
