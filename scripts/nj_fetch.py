#!/usr/bin/env python3
"""NJ fetcher: taxrecords-nj.com (NJ county boards of taxation CGI).

Source recon 2026-10-19:
  - POST https://taxrecords-nj.com/pub/cgi/inf.cgi
    ms_user=ctbNN, select_cc=NN01, district=NN00 (whole county),
    srch_type=1 (current owners/assmt), out_type=3 (Excel/CSV zip),
    owner=<SURNAME>  — SUBSTRING match on owner name (GARG -> GARGIULO)
  - response links ../download/<zip>; the zip contains the FULL result
    set as one CSV regardless of ms_ln (verified 5,643 rows)
  - owner format "PATEL (ETAL), BHARATKU" = SURNAME FIRST — the v2
    position-aware classifier applies unchanged
  - Latitude/Longitude columns exist but are 0 — coords come from
    scripts/nj_geocode.py afterwards
  - county coverage is PARTIAL: 11 full + 3 partial live, 7 counties run
    their own portals (ATLANTIC CAPE MAY HUNTERDON MERCER MONMOUTH OCEAN
    SUSSEX dead here; CAMDEN/ESSEX/SOMERSET partial). data/nj_counties.json
    holds live district lists; re-probe to extend.

Stores EVERY fetched owner row (hindu or not — user direction: keep all in
DB, export only Indian). PK = (county, block, lot, qual, account).

Usage:
  python scripts/nj_fetch.py                 # full walk (resume-aware)
  python scripts/nj_fetch.py --refresh       # ignore log, re-walk
  python scripts/nj_fetch.py --probe         # re-probe live counties
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
import zipfile

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "nj_properties.db")
COUNTIES_PATH = os.path.join(ROOT, "data", "nj_counties.json")
DICT_PATH = os.path.join(ROOT, "data", "hindu_names_dict.json")

BASE = "https://taxrecords-nj.com/pub/cgi"
DL = "https://taxrecords-nj.com/pub/download"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/155.0.0.0 Safari/537.36")
SLEEP = 0.4

# homograph surnames that substring-match enormous western sets — same
# policy as tx_fetch SKIP_SURNAMES
SKIP_SURNAMES = {
    "THOMAS", "JOHN", "GEORGE", "MARK", "LUKE", "MARTIN", "DEAN",
    "MAN", "MIN", "PAUL", "RAY", "ROY", "SEN", "DAR", "DEV", "GILL",
    "MANN", "JAY", "ANAND", "SETH",
}

# counties on this server with real data (from coverage sweep 2026-10-19,
# data/nj_counties_probe.json). partial = subset of municipalities.
LIVE_COUNTIES = {
    2: ("BERGEN", "full"), 3: ("BURLINGTON", "full"), 6: ("CUMBERLAND", "full"),
    8: ("GLOUCESTER", "full"), 9: ("HUDSON", "full"), 12: ("MIDDLESEX", "full"),
    14: ("MORRIS", "full"), 16: ("PASSAIC", "full"), 17: ("SALEM", "full"),
    20: ("UNION", "full"), 21: ("WARREN", "full"),
    4: ("CAMDEN", "partial"), 7: ("ESSEX", "partial"), 18: ("SOMERSET", "partial"),
}


def load_districts():
    """county -> list of live district codes (ALL code first)."""
    if os.path.exists(COUNTIES_PATH):
        return json.load(open(COUNTIES_PATH))
    return {}


def surnames():
    d = json.load(open(DICT_PATH))
    pool = (set(d["hindu_last_names"]) | set(d["sikh_names"])
            - set(d.get("ambiguous_last_names", [])))  # ambiguous later
    # NOTE: | binds looser than -; the line above is A | (B - C) which is
    # fine — ambiguous surnames re-removed below anyway
    pool = (set(d["hindu_last_names"]) | set(d["sikh_names"]))
    pool -= set(d.get("ambiguous_last_names", []))
    pool -= SKIP_SURNAMES
    return sorted(pool)


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS nj_properties (
            county TEXT NOT NULL,
            district TEXT,
            block TEXT, lot TEXT, qual TEXT,
            property_id TEXT PRIMARY KEY,
            owner_name TEXT,
            mailing_address TEXT,
            city_state_zip TEXT,
            property_location TEXT,
            prop_class TEXT,
            land_assmnt INTEGER, building_assmnt INTEGER, total_assmnt INTEGER,
            taxes REAL,
            sale_date TEXT, sale_price INTEGER,
            year_built TEXT, sq_ft TEXT, acreage TEXT,
            is_hindu INTEGER DEFAULT 0,
            is_indian INTEGER DEFAULT 0,
            category TEXT,
            first_seen TEXT, last_seen TEXT,
            latitude REAL, longitude REAL, geo_method TEXT
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS nj_fetch_log (
            county TEXT, query TEXT,
            count INTEGER, rows_stored INTEGER,
            fetched_at TEXT,
            PRIMARY KEY (county, query))""")
    con.commit()
    return con


def fetch_csv(sess, n, query):
    """POST owner search for whole county; return parsed CSV rows."""
    data = dict(ms_user=f"ctb{n}", passwd="", srch_type="1",
                select_cc=f"{n:02d}01", district=f"{n:02d}00",
                adv="1", out_type="3", ms_ln="1000",
                owner=query, block="", lot="", qual="", p_loc="", pageno="1")
    for attempt in range(3):
        try:
            r = sess.post(f"{BASE}/inf.cgi", data=data, timeout=120)
            m = re.search(r"(\d+) Records Found", r.text)
            z = re.search(r"href=\.\./download/(\S+?\.zip)", r.text)
            if m is None:
                return None, 0  # form error — treat as empty/broken
            total = int(m.group(1))
            if total == 0 or not z:
                return [], 0
            rz = sess.get(f"{DL}/{z.group(1)}", timeout=180)
            zf = zipfile.ZipFile(io.BytesIO(rz.content))
            name = zf.namelist()[0]
            # duplicate column names across prior-year blocks — keep FIRST
            # occurrence (current year), unlike csv.DictReader
            import csv as _csv
            rdr = _csv.reader(io.TextIOWrapper(
                zf.open(name), encoding="utf-8", errors="replace"))
            header = next(rdr)
            first = {}
            for i, h in enumerate(header):
                first.setdefault(h, i)
            rows = [{h: (vals[i] if i < len(vals) else "")
                     for h, i in first.items()} for vals in rdr]
            return rows, total
        except requests.RequestException:
            time.sleep(3 * (attempt + 1))
    return None, -1  # hard fail


def num(v):
    v = (v or "").replace(",", "").strip()
    return int(v) if v.isdigit() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--since-hours", type=float, default=None)
    args = ap.parse_args()

    from src.community_pipeline import RaceEthnicityPredictor
    predictor = RaceEthnicityPredictor()
    predictor.use_ethnicolr = False

    con = connect()
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})

    q_list = surnames()
    print(f"{len(q_list)} surnames x {len(LIVE_COUNTIES)} live counties")

    cutoff = None
    if args.since_hours and not args.refresh:
        cutoff = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - args.since_hours * 3600))
        done = {c for c, _ in con.execute(
            "SELECT 1 FROM nj_fetch_log WHERE fetched_at >= ?", (cutoff,))}
        print(f"resume: {len(done)} (county,query) pairs in window")

    grand = 0
    for n, (name, mode) in sorted(LIVE_COUNTIES.items()):
        for q in q_list:
            if not args.refresh and con.execute(
                    "SELECT 1 FROM nj_fetch_log WHERE county=? AND query=?"
                    + (" AND fetched_at>=?" if cutoff else ""),
                    ((name, q, cutoff) if cutoff else (name, q))).fetchone():
                continue
            rows, total = fetch_csv(sess, n, q)
            if rows is None:
                if total == 0:
                    print(f"  {name} {q}: form error, skipping")
                    con.execute("INSERT OR REPLACE INTO nj_fetch_log VALUES (?,?,0,0,datetime('now'))",
                                (name, q))
                    con.commit()
                    continue
                print(f"  {name} {q}: HARD FAIL after retries")
                time.sleep(5)
                continue
            stored = 0
            now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            for r in rows:
                on = (r.get("Owner's Name") or "").strip()
                if not on:
                    continue
                pid = " | ".join(filter(None, [
                    r.get("Municipality", ""), r.get("Block", ""),
                    r.get("Lot", ""), r.get("Qual", ""), r.get("Account", "")]))
                if not pid.strip(" |"):
                    continue
                res = predictor.predict_race(on)
                # strip the trailing prior-year owner blocks: CSV repeats
                # owner/mailing for 4 prior years — take the current only
                cur = con.execute(
                    "INSERT OR IGNORE INTO nj_properties "
                    "(county, district, block, lot, qual, property_id, owner_name,"
                    " mailing_address, city_state_zip, property_location, prop_class,"
                    " land_assmnt, building_assmnt, total_assmnt, taxes,"
                    " sale_date, sale_price, year_built, sq_ft, acreage,"
                    " is_hindu, is_indian, category, first_seen, last_seen)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, r.get("Municipality", ""), r.get("Block", ""),
                     r.get("Lot", ""), r.get("Qual", ""), pid, on,
                     r.get("Owner's Mailing Address", ""),
                     r.get("City/State/Zip", ""), r.get("Property Location", ""),
                     r.get("Property Class", ""),
                     num(r.get("Land Assmnt")), num(r.get("Building Assmnt")),
                     num(r.get("Total Assmnt")),
                     float(r["Taxes 1"].replace(",", "")) if (r.get("Taxes 1") or "").strip().replace(",", "").replace(".", "").isdigit() else None,
                     r.get("Sale Date", ""), num(r.get("Sale Price")),
                     r.get("Yr. Built", ""), r.get("Sq. Ft.", ""), r.get("Acreage", ""),
                     1 if res.get("is_hindu") else 0,
                     1 if res.get("is_indian") else 0,
                     res.get("sub_category") or "",
                     now, now))
                if cur.rowcount:
                    stored += 1
            grand += stored
            con.execute("INSERT OR REPLACE INTO nj_fetch_log VALUES (?,?,?,?,datetime('now'))",
                        (name, q, total, stored))
            con.commit()
            if stored:
                print(f"  {name} {q}: {total} found, +{stored} new")
            time.sleep(SLEEP)

    tot = con.execute("SELECT COUNT(*), SUM(is_hindu) FROM nj_properties").fetchone()
    print(f"\nTOTAL {tot[0]:,} rows, {tot[1] or 0:,} hindu")
    con.close()


if __name__ == "__main__":
    main()
