#!/usr/bin/env python3
"""Statewide coverage report: streets and properties, scraped vs universe.

As a demographic/GIS exercise the denominators must come from an
authoritative parcel source, not from the scrape input list — the input
(Census TIGER FULLNAME derived) carries ~170k junk/historic names that
will never match a parcel and would make coverage look artificially low.

Universe (data/md_bulk.db — MDP/Socrata ed4q-f8tm, built by
bulk_backfill.py):
  - streets:  DISTINCT (street, county) — every street name with >=1
    real parcel. This is exactly the set SDAT can return results for.
  - properties: 2.44M parcel rows = every real-property account with a
    situs address in the state.

Scraped side (data/property_search.db):
  - streets:  search_progress completed, normalized the same way
  - properties: rows joined back to the parcel universe by
    (county, street, house number parsed from our address). The
    account_id/map_parcel columns (browser engine, >= 2026-10-09) give
    an exact parcel join as they backfill via rotation.

Usage: python scripts/coverage_report.py [--per-county] [--json]
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_DB = os.path.join(ROOT, "data", "property_search.db")
BULK_DB = os.path.join(ROOT, "data", "md_bulk.db")

# Street-name suffixes SDAT search treats as separate grid values but the
# search name itself omits; strip for matching both sides.
_SUFFIXES = {
    "ST", "STREET", "AVE", "AVENUE", "RD", "ROAD", "DR", "DRIVE", "LN",
    "LANE", "CT", "COURT", "PL", "PLACE", "WAY", "TER", "TERRACE", "CIR",
    "CIRCLE", "BLVD", "BOULEVARD", "PKWY", "PARKWAY", "HWY", "HIGHWAY",
    "TRL", "TRAIL", "SQ", "SQUARE", "LOOP", "ROW", "RUN", "PIKE", "PASS",
    "PATH", "PT", "POINT", "CRES", "CRESCENT", "XING", "CROSSING",
}
_DIRS = {"N", "S", "E", "W", "NW", "NE", "SW", "SE"}


def norm_street(name: str) -> str:
    """Canonical form for street-name matching across the two stores."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name)).upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)          # drop punctuation/junk prefixes
    toks = s.split()
    while toks and (toks[-1] in _SUFFIXES or toks[-1] in _DIRS):
        toks.pop()                               # strip trailing suffix/direction
    while toks and toks[0] in _DIRS:
        toks.pop(0)                              # strip leading direction
    return " ".join(toks)


def norm_county(name: str) -> str:
    if not name:
        return ""
    return re.sub(r"\s*COUNTY$", "", str(name).upper().strip())


def parse_hnum(address: str):
    """Leading house number from a scraped address ('77 MAIN ST' -> 77)."""
    if not address:
        return None
    m = re.match(r"\s*(\d+)[A-Za-z]?\s+", str(address).upper())
    return int(m.group(1)) if m else None


def compute_coverage(per_county=True):
    """Return the coverage report dict (used by the CLI and the
    dashboard Coverage page)."""
    conn = sqlite3.connect(MAIN_DB)
    conn.execute("ATTACH DATABASE ? AS bulk", (BULK_DB,))
    cur = conn.cursor()

    # ---- universe ---------------------------------------------------
    bulk_streets = {}
    for street, county in cur.execute(
            "SELECT DISTINCT street, county FROM bulk.parcels "
            "WHERE street IS NOT NULL AND street != ''"):
        key = (norm_street(street), norm_county(county))
        if key[0]:
            bulk_streets[key] = True
    bulk_parcels = cur.execute("SELECT COUNT(*) FROM bulk.parcels").fetchone()[0]

    # ---- scraped streets ---------------------------------------------
    scraped = {}
    for street, county, status, props in cur.execute(
            "SELECT street_name, county, status, properties_found FROM search_progress"):
        key = (norm_street(street), norm_county(county))
        if not key[0]:
            continue
        scraped.setdefault(key, []).append((status, props or 0))

    # Covered = at least one completed scrape that RETURNED data. A
    # completed-with-zero-results street fails against a universe built
    # from parcels (the store says parcels exist — a zero means a WAF
    # drain or a name-format miss, never "no properties").
    covered = {k for k in bulk_streets
               if any(s == "completed" and p > 0
                      for s, p in scraped.get(k, ()))}

    # ---- property coverage: parcel-level join ------------------------
    # (county, street, hnum) from scraped addresses -> bulk parcels
    rows = []
    for county, street, addr in cur.execute(
            "SELECT DISTINCT county, street_name, address FROM properties"):
        h = parse_hnum(addr)
        if h is None:
            continue
        rows.append((norm_county(county), norm_street(street), h))

    # The join needs normalized bulk street — do it Python-side (2.4M rows
    # is fine in memory as tuples).
    parcel_keys = {}
    for rowid, county, street, hnum in cur.execute(
            "SELECT rowid, county, street, hnum FROM bulk.parcels "
            "WHERE street IS NOT NULL AND street != '' AND hnum IS NOT NULL"):
        k = (norm_county(county), norm_street(street), hnum)
        parcel_keys.setdefault(k, 0)
        parcel_keys[k] += 1

    scr_keys = {(c, s, h) for c, s, h in rows}
    matched_parcels = sum(n for k, n in parcel_keys.items() if k in scr_keys)

    # exact parcel join once map_parcel backfills (browser engine rows)
    exact = cur.execute(
        "SELECT COUNT(DISTINCT map_parcel) FROM properties "
        "WHERE map_parcel IS NOT NULL AND map_parcel != ''").fetchone()[0]
    exact_acct = cur.execute(
        "SELECT COUNT(DISTINCT account_id) FROM properties "
        "WHERE account_id IS NOT NULL AND account_id != ''").fetchone()[0]

    props_rows = cur.execute("SELECT COUNT(*) FROM properties").fetchone()[0]

    # ---- per-county ---------------------------------------------------
    per_county_data = {}
    if per_county:
        bulk_c = {}
        for (street, county) in bulk_streets:
            bulk_c[county] = bulk_c.get(county, 0) + 1
        cov_c = {}
        for (street, county) in covered:
            cov_c[county] = cov_c.get(county, 0) + 1
        for c in sorted(bulk_c):
            per_county_data[c] = {"universe": bulk_c[c],
                                  "scraped": cov_c.get(c, 0),
                                  "pct": round(100 * cov_c.get(c, 0) / bulk_c[c], 1)}

    report = {
        "streets": {
            "universe_parcels_streets": len(bulk_streets),
            "scraped_completed_in_universe": len(covered),
            "coverage_pct": round(100 * len(covered) / len(bulk_streets), 1),
            "attempted_total_input_streets": len(scraped),
        },
        "properties": {
            "universe_parcels": bulk_parcels,
            "scraped_rows": props_rows,
            "parcels_with_owner_via_addr_join": matched_parcels,
            "addr_join_coverage_pct": round(100 * matched_parcels / bulk_parcels, 1),
            "exact_map_parcel_keys": exact,
            "exact_account_keys": exact_acct,
        },
        "per_county": per_county_data,
    }
    conn.close()
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-county", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = compute_coverage(per_county=args.per_county)
    s, p = report["streets"], report["properties"]
    per_county = report["per_county"]

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        s, p = report["streets"], report["properties"]
        print("=" * 64)
        print("MARYLAND STATEWIDE COVERAGE — scraped vs parcel universe")
        print("=" * 64)
        print(f"STREETS (with >=1 parcel, per MDP parcel store)")
        print(f"  universe:          {s['universe_parcels_streets']:,}")
        print(f"  scraped w/ data:   {s['scraped_completed_in_universe']:,}"
              f"  ({s['coverage_pct']}%)")
        print(f"  (input list attempted: {s['attempted_total_input_streets']:,}"
              f" — includes TIGER junk names)")
        print(f"PROPERTIES (parcels)")
        print(f"  universe:          {p['universe_parcels']:,}")
        print(f"  scraped rows:      {p['scraped_rows']:,}")
        print(f"  parcels w/ owner (addr join): {p['parcels_with_owner_via_addr_join']:,}"
              f"  ({p['addr_join_coverage_pct']}%)")
        print(f"  exact parcel keys (map_parcel): {p['exact_map_parcel_keys']:,}"
              f" (backfilling via rotation)")
        if per_county:
            print("-" * 64)
            print(f"{'COUNTY':<22}{'UNIVERSE':>10}{'SCRAPED':>10}{'PCT':>8}")
            for c, d in per_county.items():
                print(f"{c:<22}{d['universe']:>10,}{d['scraped']:>10,}"
                      f"{d['pct']:>7.1f}%")
        print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
