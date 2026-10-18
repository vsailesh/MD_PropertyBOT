#!/usr/bin/env python3
"""Geocode TX hindu owners' situs via ArcGIS (same rig as MD geocode_database).

tx_properties has no coordinates — countytaxrates geoId is a parcel id.
Level ladder mirrors the MD geocoder but every query says Texas and the
result must land in TX (", TX" / "Texas" in returned address).

Cache: data/TX_Hindu_Owners.xlsx (address, county) -> lat/lon, so re-runs
skip the API. Rows missing city/zip (anderson-style situs) fall back to
county centroid.

Usage: python scripts/tx_geocode.py [--limit N] [--workers 4]
"""
import argparse
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geocode_results import clean_address_for_geocoder
from geocode_database import _thread_local
from geopy.geocoders import ArcGIS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "tx_properties.db")
CACHE_PATH = os.path.join(ROOT, "data", "TX_Hindu_Owners.xlsx")



def _get_geocoder():
    if not hasattr(_thread_local, "geocoder"):
        _thread_local.geocoder = ArcGIS(timeout=10)
    return _thread_local.geocoder


def load_cache():
    import pandas as pd
    if not os.path.exists(CACHE_PATH):
        return {}
    df = pd.read_excel(CACHE_PATH)
    cache = {}
    for _, r in df.iterrows():
        if pd.notna(r.get("Latitude")) and pd.notna(r.get("Longitude")):
            cache[(str(r["Street"]).upper().strip(),
                   str(r["County"]).upper().strip())] = (r["Latitude"], r["Longitude"])
    return cache


def queries_for(row):
    """(query, method) ladder, most specific first."""
    street, city, state, zc = row["street"], row["city"], row["state"] or "TX", row["zip"]
    county = row["county"].title()
    cleaned = clean_address_for_geocoder(street)
    out = []
    if city and zc:
        out.append((f"{cleaned}, {city}, Texas, {zc}, USA", "Level 0: Cleaned-City-Zip"))
    if zc:
        out.append((f"{cleaned}, Texas, {zc}, USA", "Level 1: Cleaned-Zip"))
    if city:
        out.append((f"{cleaned}, {city}, Texas, USA", "Level 2: Cleaned-City"))
    out.append((f"{cleaned}, {county} County, Texas, USA", "Level 4: Cleaned-County"))
    no_dir = re.sub(r"\b(NORTH|SOUTH|EAST|WEST|NORTHWEST|NORTHEAST|SOUTHWEST|SOUTHEAST|N|S|E|W)\b",
                    "", cleaned).strip()
    if no_dir != cleaned:
        out.append((f"{no_dir}, {city or county}, Texas, USA", "Level 5: Agnostic"))
    out.append((f"{county} County, Texas, USA", "Level 7: County Centroid"))
    return out


def geocode_one(row, cache):
    key = (str(row["street"]).upper().strip(), row["county"].upper())
    if key in cache:
        return row["county"], row["property_id"], *cache[key], "Cache", True
    geocoder = _get_geocoder()
    for q, method in queries_for(row):
        try:
            loc = geocoder.geocode(q)
        except Exception:
            continue
        if loc:
            addr = loc.address.upper()
            if ", TX" in addr or "TEXAS" in addr:
                return row["county"], row["property_id"], loc.latitude, loc.longitude, method, True
    return row["county"], row["property_id"], None, None, "None", False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    from tx_export import parse_situs
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")  # flips once, persists in file
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT county, property_id, situs_display FROM tx_properties
           WHERE is_hindu = 1 AND latitude IS NULL
             AND (geo_method IS NULL OR geo_method != 'Failed')
           LIMIT ?""", (args.limit,)).fetchall()
    if not rows:
        left = con.execute(
            "SELECT COUNT(*) FROM tx_properties WHERE is_hindu=1 AND latitude IS NULL").fetchone()[0]
        print("nothing to do; hindu rows missing coords:", left)
        return
    cache = load_cache()
    work = []
    for r in rows:
        street, city, state, zc = parse_situs(r["situs_display"])
        work.append({"county": r["county"], "property_id": r["property_id"],
                     "street": street, "city": city, "state": state,
                     "zip": zc})
    done = fail = cache_hits = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(geocode_one, w, cache) for w in work]
        for f in as_completed(futs):
            county, pid, lat, lon, method, ok = f.result()
            con.execute(
                "UPDATE tx_properties SET latitude=?, longitude=?, geo_method=? "
                "WHERE county=? AND property_id=?",
                (lat, lon, method if ok else None, county, pid))
            if ok:
                done += 1
                cache_hits += (method == "Cache")
            else:
                fail += 1
                method = "Failed"  # skip on next run (retry: clear geo_method)
    con.commit()
    left = con.execute(
        "SELECT COUNT(*) FROM tx_properties WHERE is_hindu=1 AND latitude IS NULL").fetchone()[0]
    print(f"geocoded {done}/{len(work)} (cache {cache_hits}, fail {fail}) "
          f"in {time.time()-t0:.0f}s | hindu rows still missing: {left}")
    con.close()


if __name__ == "__main__":
    main()
