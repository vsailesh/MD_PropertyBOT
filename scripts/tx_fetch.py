#!/usr/bin/env python3
"""Enumerate likely-Indian-owned TX parcels from countytaxrates.com (BIS TNT).

Recon 2026-10-17 (see memory tx-countytaxrates-recon):
  - 130/134 county subdomains live; JSON API, no auth/captcha
  - GET {HOST_API}PropertyDetails?limit=500&page=N&sort=OwnerName&searchField=
      &searchOwnerName=<q>&streetNumber=&streetName=
  - substring match on ownerName only (PAT -> PATTON); SURNAME FIRST format
    (same as SDAT) -> src/community_pipeline.py v2 classifier applies as-is
  - pagination: ~485-488 rows/page at limit=500; page past last -> HTTP 400
  - zero results -> 200 {"propertyDetails": [], "count": 0}

Strategy: surname-driven enumeration. Every surname in the hindu dict
(plus sikh names) is queried per county via searchOwnerName; rows are
classified with the v2 position-aware classifier and stored.

Pure-English homographs (THOMAS/JOHN/PAUL...) are skipped: substring
matching makes them huge (THOMPSON!) and their hindu yield is ~0.

Resume: tx_fetch_log (county, query) rows are skipped on restart, so the
script is idempotent across kills. Re-running with --refresh re-queries
everything and upserts (new parcels inserted, existing refreshed).

Usage:
  python scripts/tx_fetch.py                       # all counties, resume
  python scripts/tx_fetch.py --county collin        # one county
  python scripts/tx_fetch.py --county collin --only PATEL GUPTA
  python scripts/tx_fetch.py --refresh              # ignore fetch log
  python scripts/tx_fetch.py --threads 8
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "tx_properties.db")
COUNTIES_PATH = os.path.join(ROOT, "data", "tx_counties.json")
DICT_PATH = os.path.join(ROOT, "data", "hindu_names_dict.json")

REQ_SLEEP = 0.35          # per-worker gap between HTTP calls (polite)
PAGE_LIMIT = 500
MAX_PAGES = 40            # 40 * ~488 = ~19.5k rows per surname-county

# Substring matching + English homograph = enormous western result sets,
# near-zero hindu yield (Indian PAULs are christian, not our target).
SKIP_SURNAMES = {"THOMAS", "JOHN", "GEORGE", "MARK", "LUKE", "MARTIN",
                 "DEAN", "MAN", "MIN", "PAUL"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tx_properties (
  county TEXT NOT NULL,
  property_id TEXT NOT NULL,
  owner_id_public TEXT,
  owner_name TEXT,
  doing_business_as TEXT,
  situs_display TEXT,
  market_value REAL,
  geo_id TEXT,
  is_hindu INTEGER,
  is_indian INTEGER,
  category TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY (county, property_id)
);
CREATE INDEX IF NOT EXISTS idx_tx_hindu ON tx_properties(is_hindu);
CREATE TABLE IF NOT EXISTS tx_fetch_log (
  county TEXT NOT NULL,
  query TEXT NOT NULL,
  count INTEGER,
  pages INTEGER,
  rows_stored INTEGER,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (county, query)
);
"""


def load_surnames():
    with open(DICT_PATH) as f:
        d = json.load(f)
    names = set(d.get("hindu_last_names", [])) | set(d.get("sikh_names", []))
    return sorted(n for n in names if len(n) > 2 and n not in SKIP_SURNAMES)


def county_api(county):
    """Resolve the per-county HOST_API from the SPA config (case varies)."""
    r = requests.get(f"https://{county}.countytaxrates.com/assets/ui-config.json",
                     timeout=10)
    r.raise_for_status()
    return r.json()["HOST_API"]


def fetch_query(api, query, session):
    """Walk all pages for one surname. Returns (rows, pages, count).

    Per-page retry cap: a county API that's hard-down (5xx storm, like
    dallas's broken Lucene index) would otherwise retry the same page
    forever and wedge the whole run.
    """
    rows, page, total, retries = [], 1, 0, 0
    while page <= MAX_PAGES:
        if retries > 4:
            break  # give up this surname-county; log keeps count so far
        try:
            r = session.get(api + "PropertyDetails",
                            params={"limit": PAGE_LIMIT, "page": page,
                                    "sort": "OwnerName", "searchField": "",
                                    "searchOwnerName": query,
                                    "streetNumber": "", "streetName": ""},
                            timeout=25)
        except requests.RequestException:
            retries += 1
            time.sleep(3)
            continue  # retry same page
        if r.status_code == 400:
            break  # past last page
        if r.status_code != 200:
            retries += 1
            time.sleep(3)
            continue
        try:
            d = r.json()
        except ValueError:
            retries += 1
            time.sleep(3)
            continue
        retries = 0
        batch = d.get("propertyDetails") or []
        if not batch:
            break
        total = d.get("count", total)
        rows.extend(batch)
        if len(rows) >= total:
            break
        page += 1
        time.sleep(REQ_SLEEP)
    return rows, page, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", help="single county slug (e.g. collin)")
    ap.add_argument("--only", nargs="*", help="restrict surnames (debug)")
    ap.add_argument("--refresh", action="store_true",
                    help="re-query even if fetch log has the (county, query)")
    ap.add_argument("--since-hours", type=float, default=None,
                    help="with --refresh: still skip (county, query) pairs "
                         "logged within the last N hours — resumable refresh")
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()

    from src.community_pipeline import RaceEthnicityPredictor
    predictor = RaceEthnicityPredictor()
    predictor.use_ethnicolr = False

    with open(COUNTIES_PATH) as f:
        counties = [c["county"] for c in json.load(f)["counties"]]
    if args.county:
        counties = [args.county]

    surnames = load_surnames()
    if args.only:
        surnames = [s for s in surnames if s in set(args.only)]
    print(f"{len(counties)} counties x {len(surnames)} surnames")

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")  # flips once, persists in file
    con.executescript(SCHEMA)
    con.commit()

    def done_pairs():
        return set(r[0] for r in con.execute(
            "SELECT county || '|' || query FROM tx_fetch_log"))

    skip = set() if args.refresh else done_pairs()
    if args.refresh and args.since_hours is not None:
        # resumable refresh: pairs logged recently are fresh enough
        cutoff = (datetime.now() - timedelta(hours=args.since_hours)).isoformat(
            timespec="seconds")
        skip = {p for (p,) in con.execute(
            "SELECT county || '|' || query FROM tx_fetch_log WHERE fetched_at >= ?",
            (cutoff,))}

    for county in counties:
        try:
            api = county_api(county)
        except Exception as e:
            print(f"[{county}] config failed: {e}", flush=True)
            continue
        session = requests.Session()
        session.headers["User-Agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36")
        jobs = [(q, ) for q in surnames if f"{county}|{q}" not in skip]
        if not jobs:
            print(f"[{county}] fully done, skip", flush=True)
            continue
        t0, n_new, n_upd, n_q = time.time(), 0, 0, 0

        def work(job):
            (q,) = job
            rows, pages, total = fetch_query(api, q, session)
            return q, rows, pages, total

        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            for q, rows, pages, total in ex.map(work, jobs):
                now = datetime.now().isoformat(timespec="seconds")
                stored = 0
                for row in rows:
                    name = (row.get("ownerName") or "").strip()
                    if not name:
                        continue
                    res = predictor.predict_race(name)
                    cur = con.execute(
                        """INSERT INTO tx_properties
                           (county, property_id, owner_id_public, owner_name,
                            doing_business_as, situs_display, market_value,
                            geo_id, is_hindu, is_indian, category,
                            first_seen, last_seen)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT (county, property_id) DO UPDATE SET
                             owner_name=excluded.owner_name,
                             market_value=excluded.market_value,
                             is_hindu=excluded.is_hindu,
                             is_indian=excluded.is_indian,
                             category=excluded.category,
                             last_seen=excluded.last_seen""",
                        (county, str(row.get("propertyId")),
                         row.get("ownerIdPublic"), name,
                         row.get("doingBusinessAs"), row.get("situsDisplay"),
                         row.get("marketValue"), row.get("geoId"),
                         1 if res.get("is_hindu") else 0,
                         1 if res.get("is_indian") else 0,
                         res.get("category"), now, now))
                    stored += cur.rowcount
                    if cur.rowcount == 1:
                        n_new += 1
                n_q += 1
                con.execute(
                    "INSERT OR REPLACE INTO tx_fetch_log VALUES (?,?,?,?,?,?)",
                    (county, q, total, pages, stored, now))
                con.commit()
        skip |= {f"{county}|{q[0]}" for q in jobs}
        tot = con.execute(
            "SELECT COUNT(*), SUM(is_hindu) FROM tx_properties WHERE county=?",
            (county,)).fetchone()
        print(f"[{county}] {n_q} queries, {n_new} new rows "
              f"({time.time()-t0:.0f}s) | county total {tot[0]:,} "
              f"hindu {tot[1] or 0:,}", flush=True)

    grand = con.execute(
        "SELECT COUNT(*), SUM(is_hindu) FROM tx_properties").fetchone()
    print(f"TOTAL {grand[0]:,} rows, {grand[1] or 0:,} hindu-flagged")
    con.close()


if __name__ == "__main__":
    main()
