#!/usr/bin/env python3
"""
Refresh rotation: pick the N stalest searched streets for a re-scrape.

Full refresh cycle = every street in search_progress re-scraped with
replace semantics (old rows deleted, fresh owner names inserted).
Streets are rotated oldest-completed-first, so one `--limit 2000` run per
week walks the whole state every few months. Zero-result re-scrapes also
delete their stale rows (see RobustBulkSearch replace_mode).

Usage:
  python scripts/refresh_rotation.py                    # write Excel, print next step
  python scripts/refresh_rotation.py --limit 2000 --run # write + drain via watchdog
"""
import argparse
import os
import sqlite3
import subprocess
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
MAIN_DB = os.path.join(DATA_DIR, "property_search.db")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=2000,
                    help="streets per rotation (default 2000 ≈ full cycle in ~3 months)")
    ap.add_argument("--output", default=os.path.join(DATA_DIR, "refresh_rotation.xlsx"))
    ap.add_argument("--run", action="store_true",
                    help="after writing the Excel, drain it via the watchdog "
                         "(self-heals through Cloudflare blocks)")
    args = ap.parse_args()

    conn = sqlite3.connect(MAIN_DB)
    df = pd.read_sql_query(
        """SELECT street_name AS Street, county AS County
           FROM search_progress
           WHERE status = 'completed' AND completed_at IS NOT NULL
           ORDER BY completed_at ASC
           LIMIT ?""",
        conn, params=(args.limit,),
    )
    conn.close()

    df.to_excel(args.output, index=False)
    print(f"✅ {len(df):,} stalest streets → {args.output}")

    if not args.run:
        print("next: ./scripts/sdat_watchdog.sh -i %s --force --replace" % args.output)
        return 0

    rc = subprocess.call(
        ["./scripts/sdat_watchdog.sh", "-i", args.output,
         "--force", "--replace"],
        cwd=ROOT,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
