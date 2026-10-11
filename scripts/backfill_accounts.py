#!/usr/bin/env python3
"""Backfill account_id/map_parcel onto pre-browser rows via the parcel store.

The browser engine records SDAT's account on every grid row, but the 2.6M
rows scraped by the September requests engine carry none. For those, the
parcel store (md_bulk.db) can supply the account when the address is
UNAMBIGUOUS: exactly one bulk parcel matches (county, street, house
number). Ambiguous keys (condos, split lots, multiple parcels at one
address) are skipped — a wrong account is worse than none.

Run after any drain; idempotent (rows with an account are never touched).

Usage: python scripts/backfill_accounts.py [--dry]
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from coverage_report import norm_street, norm_county, parse_hnum  # noqa: E402

MAIN_DB = os.path.join(ROOT, "data", "property_search.db")
BULK_DB = os.path.join(ROOT, "data", "md_bulk.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true",
                    help="report what would change, change nothing")
    args = ap.parse_args()

    conn = sqlite3.connect(MAIN_DB)
    conn.execute("ATTACH DATABASE ? AS bulk", (BULK_DB,))
    cur = conn.cursor()

    # parcel keys: (county, street, hnum) -> (account, map_parcel) when the
    # key is unique in the store. First pass counts, second collects.
    key_count = {}
    for county, street, hnum in cur.execute(
            "SELECT county, street, hnum FROM bulk.parcels "
            "WHERE street IS NOT NULL AND street != '' AND hnum IS NOT NULL "
            "AND account_id IS NOT NULL AND account_id != ''"):
        k = (norm_county(county), norm_street(street), int(hnum))
        key_count[k] = key_count.get(k, 0) + 1

    uniq = {}
    for county, street, hnum, acct in cur.execute(
            "SELECT county, street, hnum, account_id FROM bulk.parcels "
            "WHERE street IS NOT NULL AND street != '' AND hnum IS NOT NULL "
            "AND account_id IS NOT NULL AND account_id != ''"):
        k = (norm_county(county), norm_street(street), int(hnum))
        if key_count[k] == 1:
            uniq[k] = acct

    print(f"parcel store: {len(key_count):,} address keys, "
          f"{len(uniq):,} unambiguous")

    # candidate rows: no account yet, parseable house number
    assigns = []
    no_key = 0
    ambig = 0
    for pid, county, street, addr in cur.execute(
            "SELECT id, county, street_name, address FROM properties "
            "WHERE (account_id IS NULL OR account_id = '')"):
        h = parse_hnum(addr)
        if h is None:
            continue
        k = (norm_county(county), norm_street(street), h)
        if k in uniq:
            assigns.append((uniq[k], pid))
        elif k in key_count:
            ambig += 1
        else:
            no_key += 1

    print(f"candidate rows: {len(assigns):,} unambiguous assigns, "
          f"{ambig:,} ambiguous keys skipped, {no_key:,} no parcel at key")

    if args.dry:
        return 0

    if assigns:
        cur.executemany(
            "UPDATE properties SET account_id=? WHERE id=?",
            assigns)
    conn.commit()
    print(f"assigned account_id/map_parcel to {len(assigns):,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
