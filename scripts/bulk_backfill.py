#!/usr/bin/env python3
"""
Backfill parcel coordinates + city/zip from MDP's statewide bulk dataset
(Socrata: ed4q-f8tm "Maryland Real Property Assessments") into the local DB.

Owner names are NOT in the bulk data (hidden by SDAT) — this script only
fills latitude/longitude (exact MDP parcel coords, better than geocoder
street interpolation) and empty city/zip_code columns.

Join key: (county, cleaned street name, house number) — unique-coordinate
matches only, so condo/ambiguity cases are skipped rather than misplaced.

Usage:
  python scripts/bulk_backfill.py --download        # fetch bulk -> data/md_bulk.db (resumable)
  python scripts/bulk_backfill.py --backfill        # join + update property_search.db
  python scripts/bulk_backfill.py                   # both
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.street_name_cleaner import StreetNameCleaner

SODA_CSV = "https://opendata.maryland.gov/resource/ed4q-f8tm.csv"
FIELDS = [
    "county_name_mdp_field_cntyname",
    "account_id_mdp_field_acctid",
    "premise_address_number_mdp_field_premsnum_sdat_field_20",
    "premise_address_name_mdp_field_premsnam_sdat_field_23",
    "premise_address_type_mdp_field_premstyp_sdat_field_24",
    "premise_address_city_mdp_field_premcity_sdat_field_25",
    "premise_address_zip_code_mdp_field_premzip_sdat_field_26",
    "mdp_latitude_mdp_field_digycord_converted_to_wgs84",
    "mdp_longitude_mdp_field_digxcord_converted_to_wgs84",
]

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
BULK_DB = os.path.join(DATA_DIR, "md_bulk.db")
MAIN_DB = os.path.join(DATA_DIR, "property_search.db")
PROGRESS_FILE = os.path.join(DATA_DIR, ".bulk_download_progress.json")
PAGE_SIZE = 50_000

HOUSE_NUM_RE = re.compile(r"^\s*(\d+)")
ZIP_RE = re.compile(r"^\d{5}$")
_cleaner = StreetNameCleaner()


def clean_county(name: str) -> str:
    if not name or pd.isna(name):
        return ""
    return str(name).upper().strip().removesuffix(" COUNTY").strip()


def clean_street(name: str) -> str:
    if not name or pd.isna(name):
        return ""
    return _cleaner.clean_street_name(str(name))


def parse_hnum(value) -> int | None:
    """House number: from bulk PREMSNUM ('00007') or our address ('11604 BEDFORD CT')."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s:
        return None
    m = HOUSE_NUM_RE.match(s)
    if not m:
        return None
    n = int(m.group(1))
    return n if n > 0 else None


def clean_zip(value) -> str:
    s = str(value or "").strip()
    return s if ZIP_RE.match(s) else ""


# ---------------------------------------------------------------- download

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"offset": 0}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)


def download(max_pages: int | None = None, dry_run: bool = False):
    """Page through the SODA dataset, storing cleaned rows in data/md_bulk.db."""
    conn = sqlite3.connect(BULK_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parcels (
            account_id TEXT PRIMARY KEY,
            county     TEXT,
            street     TEXT,
            hnum       INTEGER,
            stype      TEXT,
            city       TEXT,
            zip        TEXT,
            lat        REAL,
            lon        REAL
        )
    """)
    conn.commit()

    progress = load_progress()
    offset = progress["offset"]
    select = ",".join(FIELDS)

    session = requests.Session()
    page = 0
    total_stored = conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
    print(f"⬇️  Downloading bulk parcels (resuming from offset {offset:,}, {total_stored:,} stored)")

    while True:
        if max_pages and page >= max_pages:
            print(f"\n  page limit ({max_pages}) reached — stopping")
            break

        params = {
            "$select": select,
            "$order": "account_id_mdp_field_acctid",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        }
        for attempt in range(5):
            try:
                resp = session.get(SODA_CSV, params=params, timeout=300)
                resp.raise_for_status()
                break
            except Exception as e:
                wait = min(2 ** attempt, 30)
                print(f"\n  ⚠ page fetch error ({type(e).__name__}), retry in {wait}s")
                time.sleep(wait)
        else:
            print("\n  ❌ page failed after retries — re-run to resume")
            break

        df = pd.read_csv(io.StringIO(resp.text), dtype=str)
        if df.empty:
            print(f"\n  ✅ end of dataset at offset {offset:,}")
            progress["offset"] = offset
            progress["done"] = True
            save_progress(progress)
            break

        rows = []
        for r in df.itertuples(index=False):
            county, acct, premsnum, premsnam, premstyp, premcity, premzip, lat, lon = r
            try:
                lat_f = float(lat) if lat and not pd.isna(lat) else None
                lon_f = float(lon) if lon and not pd.isna(lon) else None
            except (ValueError, TypeError):
                lat_f = lon_f = None
            rows.append((
                acct,
                clean_county(county),
                clean_street(premsnam),
                parse_hnum(premsnum),
                str(premstyp).strip() if premstyp and not pd.isna(premstyp) else "",
                str(premcity).strip().upper() if premcity and not pd.isna(premcity) else "",
                clean_zip(premzip),
                lat_f,
                lon_f,
            ))

        if not dry_run:
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO parcels VALUES (?,?,?,?,?,?,?,?,?)", rows
                )
            offset += len(df)
            progress["offset"] = offset
            save_progress(progress)
            total_stored += len(rows)

        page += 1
        elapsed_rate = page * PAGE_SIZE / max(time.time() - (download._t0 or time.time()), 1)
        print(f"\r  📥 offset {offset:,} | stored {total_stored:,} rows", end="", flush=True)

    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
    geo = conn.execute("SELECT COUNT(*) FROM parcels WHERE lat IS NOT NULL").fetchone()[0]
    conn.close()
    print(f"\n✅ bulk store: {n:,} parcels, {geo:,} with coords")


# ---------------------------------------------------------------- backfill

def backfill(batch_size: int = 10_000):
    conn = sqlite3.connect(MAIN_DB)
    conn.execute("ATTACH DATABASE ? AS bulk", (BULK_DB,))
    cur = conn.cursor()

    need = cur.execute(
        "SELECT COUNT(*) FROM main.properties WHERE (latitude IS NULL OR city IS NULL OR city='')"
    ).fetchone()[0]
    print(f"📊 properties needing coords/city: {need:,}")

    # 1. keymap: only keys whose parcels agree on one coordinate location
    print("🔨 building unique-coordinate keymap ...")
    cur.execute("DROP TABLE IF EXISTS temp.km")
    cur.execute("""
        CREATE TABLE temp.km AS
        SELECT county, street, hnum,
               MIN(lat) AS lat, MIN(lon) AS lon,
               MIN(city) AS city, MIN(zip) AS zip,
               COUNT(*) AS n_parcels
        FROM bulk.parcels
        WHERE lat IS NOT NULL AND street != '' AND hnum IS NOT NULL
        GROUP BY county, street, hnum
        HAVING MIN(lat) = MAX(lat) AND MIN(lon) = MAX(lon)
    """)
    cur.execute("CREATE INDEX temp.idx_km ON km(county, street, hnum)")
    keys = cur.execute("SELECT COUNT(*) FROM temp.km").fetchone()[0]
    print(f"   {keys:,} unique-coordinate keys")

    # 2. pre-clean distinct (county, street) pairs once — 31k cleans, not 1.86M
    print("🧹 pre-cleaning distinct street keys ...")
    key_cache = {}
    for county, street in cur.execute(
        "SELECT DISTINCT county, street_name FROM main.properties"
    ):
        key_cache[(county, street)] = (clean_county(county), clean_street(street) if street else "")

    # 3. stream our properties, match, update
    # NOTE: separate cursors — reusing one makes the inner lookup execute()
    # destroy the outer streaming result (bug that stopped the loop after 1 batch).
    print("🔗 matching properties ...")
    stream = conn.cursor()
    lookup = conn.cursor()
    stream.execute("""
        SELECT id, county, street_name, address
        FROM main.properties
        WHERE latitude IS NULL OR city IS NULL OR city = ''
    """)
    coord_updates, city_updates = [], []
    matched = written = 0
    while True:
        batch = stream.fetchmany(batch_size)
        if not batch:
            break
        for pid, county, street, address in batch:
            k_county, k_street = key_cache.get((county, street), ("", ""))
            k_hnum = parse_hnum(address)
            if not (k_county and k_street and k_hnum):
                continue
            hit = lookup.execute(
                "SELECT lat, lon, city, zip FROM temp.km WHERE county=? AND street=? AND hnum=?",
                (k_county, k_street, k_hnum),
            ).fetchone()
            if not hit:
                continue
            lat, lon, bcity, bzip = hit
            matched += 1
            coord_updates.append((lat, lon, pid))
            city_updates.append((bcity, bzip, pid))

        if coord_updates:
            with conn:
                conn.executemany(
                    """UPDATE main.properties SET latitude=?, longitude=?, geo_method='MDP Parcel'
                       WHERE id=? AND latitude IS NULL""",
                    coord_updates,
                )
                conn.executemany(
                    """UPDATE main.properties SET city=COALESCE(NULLIF(?,''), city),
                                                  zip_code=COALESCE(NULLIF(?,''), zip_code)
                       WHERE id=? AND (city IS NULL OR city='')""",
                    city_updates,
                )
            written += len(coord_updates)
            coord_updates, city_updates = [], []
            print(f"\r  ✍️  matched {matched:,} | written {written:,}", end="", flush=True)

    print()

    filled = cur.execute(
        "SELECT COUNT(*) FROM main.properties WHERE geo_method='MDP Parcel'"
    ).fetchone()[0]
    still = cur.execute(
        "SELECT COUNT(*) FROM main.properties WHERE latitude IS NULL"
    ).fetchone()[0]
    cities = cur.execute(
        "SELECT COUNT(*) FROM main.properties WHERE city IS NOT NULL AND city != ''"
    ).fetchone()[0]
    conn.close()
    print(f"✅ backfill done: {filled:,} rows via MDP Parcel, {still:,} still missing coords, "
          f"{cities:,} rows have city")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--download", action="store_true", help="fetch bulk data (resumable)")
    ap.add_argument("--backfill", action="store_true", help="join + update main DB")
    ap.add_argument("--max-pages", type=int, default=None, help="download: stop after N pages")
    ap.add_argument("--dry-run", action="store_true", help="download: fetch pages, don't store")
    args = ap.parse_args()

    if not (args.download or args.backfill):
        args.download = args.backfill = True

    if args.download:
        download._t0 = time.time()
        download(max_pages=args.max_pages, dry_run=args.dry_run)
    if args.backfill:
        if not os.path.exists(BULK_DB):
            sys.exit("No bulk db — run with --download first")
        backfill()


if __name__ == "__main__":
    main()
