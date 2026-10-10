#!/usr/bin/env python3
"""Backfill coordinates/city/zip from the bulk parcel store via account_id.

The browser scraper records SDAT's account number on every grid row, and
the MDP parcel store (data/md_bulk.db, Socrata ed4q-f8tm) uses the SAME
account number — verified 298/300 exact-digit match on a live drain. That
makes coords/city/zip/hnum a pure SQL join: no geocoding API needed for
any row the browser engine has touched.

Also prefixes a missing house number onto street-level addresses
('PARK AVE' -> '8 PARK AVE') when the parcel has one, which upgrades the
approximate address-join coverage into exact parcel coverage.

Rows updated per run: everything with an account_id and no coords yet —
safe to run nightly after the watchdog drain. Idempotent.

Usage: python scripts/backfill_coords.py [--dry]
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_DB = os.path.join(ROOT, "data", "property_search.db")
BULK_DB = os.path.join(ROOT, "data", "md_bulk.db")

# SDAT's grid shows the account WITHOUT the county prefix for most
# jurisdictions (PG '14 1581511' = MDP '17'+'14'+'1581511'); Baltimore
# City accounts match raw. Lookup tries raw digits first, then the
# county-prefixed form.
COUNTY_CODE = {
    'ALLEGANY': '01', 'ANNE ARUNDEL': '02', 'BALTIMORE CITY': '03',
    'BALTIMORE COUNTY': '04', 'CALVERT': '05', 'CAROLINE': '06',
    'CARROLL': '07', 'CECIL': '08', 'CHARLES': '09', 'DORCHESTER': '10',
    'FREDERICK': '11', 'GARRETT': '12', 'HARFORD': '13', 'HOWARD': '14',
    'KENT': '15', 'MONTGOMERY': '16', "PRINCE GEORGE'S": '17',
    'QUEEN ANNE\'S': '18', 'ST. MARY\'S': '19', 'SOMERSET': '20',
    'TALBOT': '21', 'WASHINGTON': '22', 'WICOMICO': '23', 'WORCESTER': '24',
}


def county_code(county: str):
    c = (county or "").upper().strip()
    is_city = c.endswith(" CITY")
    for s in (" COUNTY", " CITY"):
        if c.endswith(s):
            c = c[: -len(s)].strip()
            break
    return COUNTY_CODE.get(c + (" CITY" if is_city else "")) or COUNTY_CODE.get(c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true",
                    help="report what would change, change nothing")
    args = ap.parse_args()

    conn = sqlite3.connect(MAIN_DB)
    conn.execute("ATTACH DATABASE ? AS bulk", (BULK_DB,))
    cur = conn.cursor()

    # candidate rows: browser-engine rows still missing coords
    rows = cur.execute("""
        SELECT p.id, p.account_id, p.address, p.latitude, p.city, p.county
        FROM properties p
        WHERE p.account_id IS NOT NULL AND p.account_id != ''
          AND (p.latitude IS NULL OR p.city IS NULL OR p.city = '')
    """).fetchall()
    if not rows:
        print("nothing to backfill (0 rows missing coords with account_id)")
        return 0

    # one bulk lookup per distinct (county, account) — raw digits first,
    # then county-prefixed (see COUNTY_CODE note above)
    lookup_cache = {}

    def bulk_lookup(county, acct):
        key = (county, acct)
        if key in lookup_cache:
            return lookup_cache[key]
        d = acct.replace(" ", "")
        b = cur.execute(
            """SELECT lat, lon, city, zip, hnum, street FROM bulk.parcels
               WHERE account_id = ?""", (d,)).fetchone()
        if not b:
            cc = county_code(county)
            if cc and not d.startswith(cc):
                b = cur.execute(
                    """SELECT lat, lon, city, zip, hnum, street FROM bulk.parcels
                       WHERE account_id = ?""", (cc + d,)).fetchone()
        lookup_cache[key] = b
        return b

    upd_coord = 0
    upd_addr = 0
    no_match = 0
    coord_sql = "UPDATE properties SET latitude=?, longitude=?, city=?, zip_code=? WHERE id=?"
    addr_sql = "UPDATE properties SET address=? WHERE id=?"
    coord_params, addr_params = [], []
    for pid, acct, address, lat, city, county in rows:
        b = bulk_lookup(county, acct)
        if not b:
            no_match += 1
            continue
        blat, blon, bcity, bzip, bhnum, bstreet = b
        if lat is None:
            coord_params.append((blat, blon, bcity or "", bzip or "", pid))
        # house-number enrichment: no leading number on our address but
        # the parcel knows the exact one (grid rows can be street-level)
        addr = (address or "").lstrip()
        if bhnum and (not addr or not addr[0].isdigit()):
            # rebuild as 'HNUM <grid address>' — grid address already
            # carries the street suffix; avoids stitching bulk stype
            new_addr = f"{bhnum} {addr}".strip()
            addr_params.append((new_addr, pid))

    if args.dry:
        print(f"dry: {len(rows)} candidates, {len(coord_params)} coord "
              f"updates, {len(addr_params)} address enrichments, "
              f"{no_match} unmatched accounts")
        return 0

    if coord_params:
        upd_coord = cur.executemany(coord_sql, coord_params).rowcount
    if addr_params:
        # address is part of UNIQUE(county, owner_name, address) — an
        # enrichment can collide with an existing row (same owner already
        # at that exact address); drop those individually, keep the rest
        try:
            upd_addr = cur.executemany(addr_sql, addr_params).rowcount
        except sqlite3.IntegrityError:
            for p in addr_params:
                try:
                    upd_addr += cur.execute(addr_sql, p).rowcount
                except sqlite3.IntegrityError:
                    pass
    conn.commit()

    print(f"backfilled coords/city/zip: {len(coord_params)} rows "
          f"({upd_coord} updated)")
    print(f"enriched street-level addresses with house numbers: "
          f"{len(addr_params)} rows ({upd_addr} updated)")
    print(f"unmatched accounts: {no_match} (parcel store has no such account)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
