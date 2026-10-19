#!/usr/bin/env python3
"""Geocode NJ hindu properties via ArcGIS (sibling of tx_geocode.py).

Source CSV carries Latitude/Longitude columns but they're 0 — all coords
come from here. Queries say New Jersey; results must land in NJ.

Usage: python scripts/nj_geocode.py [--limit N] [--workers N]
"""
import argparse
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "nj_properties.db")

ARCGIS = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
UA = "maryland-property-agent/1.0 (research)"

# progressively coarser query shapes; every query pins New Jersey
def level_queries(row):
    loc = (row["property_location"] or "").strip()
    mun = (row["district"] or "").strip()
    zipc = ""
    m = re.search(r"(\d{5})(?:-\d{4})?\s*$", row["city_state_zip"] or "")
    if m:
        zipc = m.group(1)
    qs = []
    if loc and mun:
        qs.append(f"{loc}, {mun}, NJ")
    if loc and zipc:
        qs.append(f"{loc}, NJ {zipc}")
    if loc:
        qs.append(f"{loc}, NJ")
    if mun:
        qs.append(f"{mun}, NJ")
    return qs


def geocode(sess, q):
    r = sess.get(ARCGIS, params={
        "f": "json", "singleLine": q, "maxLocations": 1, "outFields": ""},
        timeout=30)
    r.raise_for_status()
    cands = r.json().get("candidates", [])
    if not cands:
        return None
    c = cands[0]
    addr = (c.get("address", "") or "").upper()
    if ", NJ" not in addr and "NEW JERSEY" not in addr:
        return None
    lat, lng = c["location"]["y"], c["location"]["x"]
    if not (38.8 < lat < 41.4 and -75.6 < lng < -73.8):
        return None
    return lat, lng, c.get("score", 0), addr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    sql = ("SELECT property_id, property_location, district, city_state_zip "
           "FROM nj_properties WHERE is_hindu=1 AND latitude IS NULL "
           "AND (geo_method IS NULL OR geo_method != 'Failed')")
    rows = con.execute(sql).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows):,} hindu rows missing coords")
    if not rows:
        return

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})

    def work(row):
        # returns update tuple; DB write happens on the main thread (sqlite
        # connections are thread-bound)
        pid, loc, mun, csz = row
        lat = lng = None
        method = None
        for i, q in enumerate(level_queries(
                {"property_location": loc, "district": mun, "city_state_zip": csz})):
            try:
                got = geocode(sess, q)
            except requests.RequestException:
                time.sleep(2)
                continue
            if got:
                lat, lng, score, addr = got
                method = f"street L{i}" if i < 2 else f"fallback L{i}"
                break
        if lat is None:
            method = "Failed"
        return (lat, lng, method, pid)

    ok = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (lat, lng, method, pid) in enumerate(ex.map(work, rows), 1):
            ok += lat is not None
            con.execute("UPDATE nj_properties SET latitude=?, longitude=?, geo_method=? "
                        "WHERE property_id=?", (lat, lng, method, pid))
            if i % 200 == 0:
                con.commit()
                print(f"  {i}/{len(rows)} | {ok} ok", flush=True)
    con.commit()
    print(f"geocoded {ok}/{len(rows)}")


if __name__ == "__main__":
    main()
