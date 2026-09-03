#!/usr/bin/env python3
"""
Coverage audit: our scraped properties vs MDP bulk parcel ground truth.

Compares DISTINCT house numbers per (county, street) — parcel counts
overstate gaps (condos/vacant land share addresses), addresses are what
we actually collect.

Outputs:
  - console summary + worst gaps
  - --gaps FILE: Excel (Street/County) of under-covered streets, ready as
    `robust_bulk_search.py run -i FILE` input for a self-healing re-scrape

Usage:
  python scripts/coverage_audit.py                 # summary only
  python scripts/coverage_audit.py --gaps data/coverage_gaps.xlsx
"""
import argparse
import os
import re
import sqlite3
import sys

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MAIN_DB = os.path.join(DATA_DIR, "property_search.db")
BULK_DB = os.path.join(DATA_DIR, "md_bulk.db")

AUDIT_SQL = """
    WITH ours AS (
        SELECT upper(replace(county,' COUNTY','')) c, street_name s,
               county county_raw,
               COUNT(DISTINCT hnum(address)) n_ours
        FROM properties GROUP BY 1, 2
    ),
    theirs AS (
        SELECT county c, street s, COUNT(DISTINCT hnum) n_bulk
        FROM bulk.parcels WHERE street != '' AND hnum IS NOT NULL
        GROUP BY 1, 2
    )
    SELECT ours.c, ours.s, ours.county_raw, n_ours, n_bulk
    FROM ours JOIN theirs ON ours.s = theirs.s AND ours.c = theirs.c
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gaps", default=None, help="write gap-street list Excel to this path")
    ap.add_argument("--min-gap", type=int, default=1,
                    help="only list streets missing at least N house numbers")
    args = ap.parse_args()

    main_db = sqlite3.connect(MAIN_DB)
    main_db.execute("ATTACH DATABASE ? AS bulk", (BULK_DB,))
    main_db.create_function(
        "hnum", 1,
        lambda a: int(m.group(1)) if a and (m := re.match(r"\s*(\d+)", str(a))) and int(m.group(1)) > 0 else None,
    )

    df = pd.read_sql_query(AUDIT_SQL, main_db)
    main_db.close()

    total = len(df)
    covered = int((df.n_ours >= df.n_bulk).sum())
    gaps = df[df.n_ours < df.n_bulk].copy()
    missing = int((df.n_bulk - df.n_ours).clip(lower=0).sum())

    print(f"street keys compared : {total:,}")
    print(f"fully covered        : {covered:,} ({covered/total*100:.1f}%)")
    print(f"streets w/ gaps      : {len(gaps):,}")
    print(f"missing house numbers: {missing:,}")

    if args.gaps:
        out = gaps[gaps.n_bulk - gaps.n_ours >= args.min_gap].sort_values(
            ["n_bulk", "n_ours"], ascending=[False, True])
        # county_raw keeps the suffix our county_map needs (COUNTY vs CITY)
        pd.DataFrame({"Street": out["s"], "County": out["county_raw"]}).to_excel(
            args.gaps, index=False)
        print(f"✅ wrote {len(out):,} gap streets to {args.gaps}")


if __name__ == "__main__":
    sys.exit(main())
